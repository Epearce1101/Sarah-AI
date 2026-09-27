"""Wake-word phrase matching helpers."""
from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any

from backend.config import settings


@dataclass
class WakeMatcherState:
    """Mutable state for cooldown and prefix-hit fallback matching."""

    last_wake_at: float = -9999.0
    prefix_hits: list[float] = field(default_factory=list)


@dataclass(frozen=True)
class WakeMatchResult:
    """Structured wake matching outcome for logs and tests."""

    wake: bool
    match: str | None = None
    match_type: str = "none"
    reason: str = "no_match"
    wake_reason: str = "no_match"
    confidence_state: str = "unknown"
    confidence: float | None = None
    prefix_hit_count: int = 0


def normalize_wake_text(text: str) -> str:
    """Normalize Vosk transcript text for phrase matching."""
    cleaned = re.sub(r"[^a-z0-9 ]+", " ", (text or "").lower())
    return re.sub(r"\s+", " ", cleaned).strip()


def get_wake_words() -> tuple[str, ...]:
    """Return configured wake phrases in normalized form."""
    words: list[str] = []
    seen: set[str] = set()
    for phrase in settings.wake_words:
        for variant in expand_wake_phrase_variants(phrase):
            normalized = normalize_wake_text(variant)
            if normalized and normalized not in seen:
                seen.add(normalized)
                words.append(normalized)
    return tuple(words)


def expand_wake_phrase_variants(phrase: str) -> tuple[str, ...]:
    """Return common Vosk spellings for Sarah wake phrases."""
    normalized = normalize_wake_text(phrase)
    if not normalized:
        return ()

    variants = {normalized}
    sarah_spellings = ("sarah", "sara", "sera", "saira")
    for spelling in sarah_spellings:
        if "sarah" in normalized:
            variants.add(normalized.replace("sarah", spelling))

    # Vosk often misses the leading "hey/ok" or hears "okay" as "ok".
    more = set()
    for item in variants:
        more.add(item.replace("okay ", "ok "))
        more.add(item.replace("ok ", "okay "))
        for prefix in ("hey ", "hi ", "hello ", "ok ", "okay ", "yo "):
            if item.startswith(prefix):
                more.add(item[len(prefix):])
    variants.update(more)
    return tuple(sorted(variants))


def get_wake_grammar() -> tuple[str, ...]:
    """Return a Vosk grammar list restricted to wake phrases and unknowns."""
    words = list(get_wake_words())
    for prefix in get_wake_prefixes():
        if prefix not in words:
            words.append(prefix)
    if "[unk]" not in words:
        words.append("[unk]")
    return tuple(words)


def get_wake_prefixes() -> tuple[str, ...]:
    """Return normalized prefix-only fallback phrases."""
    prefixes: list[str] = []
    seen: set[str] = set()
    for phrase in settings.wake_prefix_fallback_phrases:
        normalized = normalize_wake_text(phrase)
        if normalized and normalized not in seen:
            seen.add(normalized)
            prefixes.append(normalized)
    return tuple(prefixes)


def detect_wake_phrase(text: str, wake_words: tuple[str, ...] | None = None) -> str | None:
    """Return the matching wake phrase, or None if no phrase was heard."""
    transcript = normalize_wake_text(text)
    if not transcript:
        return None
    for phrase in wake_words or get_wake_words():
        if phrase in transcript:
            return phrase
    return None


def detect_wake_prefix(text: str, prefixes: tuple[str, ...] | None = None) -> str | None:
    """Return a prefix-only fallback match, or None if none was heard."""
    transcript = normalize_wake_text(text)
    if not transcript:
        return None
    for prefix in prefixes or get_wake_prefixes():
        if transcript == prefix:
            return prefix
    return None


def extract_vosk_confidence(result: dict[str, Any] | None) -> float | None:
    """Return average Vosk word confidence when the recognizer provided it."""
    if not isinstance(result, dict):
        return None
    words = result.get("result")
    if not isinstance(words, list):
        return None

    confidences: list[float] = []
    for item in words:
        if not isinstance(item, dict) or "conf" not in item:
            continue
        try:
            confidences.append(float(item["conf"]))
        except (TypeError, ValueError):
            continue
    if not confidences:
        return None
    return sum(confidences) / len(confidences)


def _confidence_state(confidence: float | None, min_confidence: float) -> str:
    if min_confidence <= 0:
        return "disabled"
    if confidence is None:
        return "unknown"
    return "pass" if confidence >= min_confidence else "block"


def _phrase_match_type(match: str) -> str:
    return "keyword" if "sarah" in match.split() else "variant"


def match_wake_transcript(
    text: str,
    state: WakeMatcherState,
    *,
    now: float,
    wake_words: tuple[str, ...] | None = None,
    prefixes: tuple[str, ...] | None = None,
    prefix_enabled: bool = True,
    allow_prefix_fallback: bool = True,
    prefix_hits_required: int = 2,
    prefix_window_seconds: float = 2.0,
    cooldown_seconds: float = 2.0,
    confidence: float | None = None,
    min_confidence: float = 0.0,
    single_prefix_confidence: float = 0.50,
) -> WakeMatchResult:
    """Match one transcript against full wake phrases and prefix fallback.

    Full Sarah phrases can wake immediately. Prefix-only fallbacks require
    repeated final transcripts inside a short window, which avoids accidental
    wakes from one casual "hey" or "hi".
    """
    matched = detect_wake_phrase(text, wake_words)
    cooldown_active = now - state.last_wake_at < max(0.0, cooldown_seconds)
    conf_state = _confidence_state(confidence, min_confidence)

    if matched:
        state.prefix_hits.clear()
        match_type = _phrase_match_type(matched)
        if conf_state == "block":
            return WakeMatchResult(False, matched, match_type, "low_confidence", "confidence_block", conf_state, confidence)
        if cooldown_active:
            return WakeMatchResult(False, matched, match_type, "cooldown", "cooldown_block", conf_state, confidence)
        state.last_wake_at = now
        return WakeMatchResult(True, matched, match_type, "matched", match_type, conf_state, confidence)

    if not prefix_enabled or not allow_prefix_fallback:
        return WakeMatchResult(False, reason="no_match", wake_reason="no_match", confidence_state=conf_state, confidence=confidence)

    prefix = detect_wake_prefix(text, prefixes)
    if not prefix:
        return WakeMatchResult(False, reason="no_match", wake_reason="no_match", confidence_state=conf_state, confidence=confidence)
    if conf_state == "block":
        return WakeMatchResult(False, prefix, "prefix_fallback", "low_confidence", "confidence_block", conf_state, confidence)
    if (
        confidence is not None
        and confidence >= max(min_confidence, single_prefix_confidence)
    ):
        if cooldown_active:
            return WakeMatchResult(False, prefix, "prefix_fallback", "cooldown", "cooldown_block", conf_state, confidence, 1)
        state.prefix_hits.clear()
        state.last_wake_at = now
        return WakeMatchResult(True, prefix, "prefix_fallback", "matched", "prefix_confidence_pass", conf_state, confidence, 1)

    window = max(0.2, prefix_window_seconds)
    needed = max(1, prefix_hits_required)
    state.prefix_hits = [hit for hit in state.prefix_hits if now - hit <= window]
    state.prefix_hits.append(now)
    hit_count = len(state.prefix_hits)

    if hit_count < needed:
        return WakeMatchResult(False, prefix, "prefix_fallback", "prefix_pending", "prefix_pending", conf_state, confidence, hit_count)
    state.prefix_hits.clear()
    if cooldown_active:
        return WakeMatchResult(False, prefix, "prefix_fallback", "cooldown", "cooldown_block", conf_state, confidence, hit_count)
    state.last_wake_at = now
    return WakeMatchResult(True, prefix, "prefix_fallback", "matched", "prefix_fallback", conf_state, confidence, hit_count)
