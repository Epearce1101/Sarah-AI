from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from backend.audio.wake_words import (
    WakeMatcherState,
    detect_wake_prefix,
    detect_wake_phrase,
    expand_wake_phrase_variants,
    extract_vosk_confidence,
    get_wake_grammar,
    get_wake_prefixes,
    get_wake_words,
    match_wake_transcript,
    normalize_wake_text,
)


def test_default_wake_words_include_multiple_variations():
    words = get_wake_words()

    assert len(words) >= 6
    assert "hey sarah" in words
    assert "hey sara" in words
    assert "ok sarah" in words
    assert "okay sarah" in words
    assert "hello sarah" in words
    assert "wake up sarah" in words
    assert "sarah" in words


def test_detect_wake_phrase_normalizes_transcript():
    words = ("hello sarah", "wake up sarah")

    assert detect_wake_phrase("Hello, Sarah!", words) == "hello sarah"
    assert detect_wake_phrase("please wake up sarah now", words) == "wake up sarah"


def test_detect_wake_phrase_accepts_common_sarah_spellings():
    assert detect_wake_phrase("hey sara", expand_wake_phrase_variants("hey sarah")) == "hey sara"
    assert detect_wake_phrase("okay sera", expand_wake_phrase_variants("okay sarah")) == "okay sera"


def test_wake_grammar_includes_unknown_escape():
    grammar = get_wake_grammar()
    assert "[unk]" in grammar
    assert "hey" in grammar
    assert "hey sarah" in grammar


def test_detect_wake_prefix_fallback_is_exact():
    prefixes = get_wake_prefixes()
    assert "hey" in prefixes
    assert detect_wake_prefix("hey", prefixes) == "hey"
    assert detect_wake_prefix("hey sarah", prefixes) is None


def test_wake_matcher_requires_two_prefix_hits():
    state = WakeMatcherState()
    words = ("sarah",)
    prefixes = ("hey",)

    first = match_wake_transcript(
        "hey",
        state,
        now=10.0,
        wake_words=words,
        prefixes=prefixes,
        prefix_hits_required=2,
    )
    second = match_wake_transcript(
        "hey",
        state,
        now=10.8,
        wake_words=words,
        prefixes=prefixes,
        prefix_hits_required=2,
    )

    assert first.wake is False
    assert first.reason == "prefix_pending"
    assert second.wake is True
    assert second.match_type == "prefix_fallback"
    assert second.wake_reason == "prefix_fallback"



def test_wake_matcher_high_confidence_prefix_can_wake_once():
    state = WakeMatcherState()
    result = match_wake_transcript(
        "hey",
        state,
        now=50.0,
        wake_words=("sarah",),
        prefixes=("hey",),
        prefix_hits_required=2,
        confidence=0.55,
        min_confidence=0.35,
        single_prefix_confidence=0.50,
    )

    assert result.wake is True
    assert result.match_type == "prefix_fallback"
    assert result.wake_reason == "prefix_confidence_pass"


def test_wake_matcher_single_prefix_missing_confidence_still_waits():
    state = WakeMatcherState()
    result = match_wake_transcript(
        "hey",
        state,
        now=55.0,
        wake_words=("sarah",),
        prefixes=("hey",),
        prefix_hits_required=2,
        confidence=None,
        min_confidence=0.35,
        single_prefix_confidence=0.50,
    )

    assert result.wake is False
    assert result.reason == "prefix_pending"
    assert result.confidence_state == "unknown"


def test_wake_matcher_direct_sarah_variant_wakes():
    state = WakeMatcherState()
    result = match_wake_transcript(
        "hello sara",
        state,
        now=20.0,
        wake_words=expand_wake_phrase_variants("hello sarah"),
        prefixes=("hello",),
    )

    assert result.wake is True
    assert result.match == "hello sara"
    assert result.match_type == "variant"
    assert result.wake_reason == "variant"


def test_wake_matcher_respects_cooldown_and_confidence():
    state = WakeMatcherState()
    words = ("sarah",)

    assert match_wake_transcript("sarah", state, now=30.0, wake_words=words).wake is True
    cooldown = match_wake_transcript(
        "sarah",
        state,
        now=31.0,
        wake_words=words,
        cooldown_seconds=2.0,
    )
    low_confidence = match_wake_transcript(
        "sarah",
        state,
        now=33.5,
        wake_words=words,
        confidence=0.2,
        min_confidence=0.35,
    )

    assert cooldown.wake is False
    assert cooldown.reason == "cooldown"
    assert cooldown.wake_reason == "cooldown_block"
    assert low_confidence.wake is False
    assert low_confidence.reason == "low_confidence"
    assert low_confidence.wake_reason == "confidence_block"


def test_wake_matcher_missing_confidence_is_unknown_not_pass():
    state = WakeMatcherState()
    result = match_wake_transcript(
        "sarah",
        state,
        now=40.0,
        wake_words=("sarah",),
        confidence=None,
        min_confidence=0.35,
    )

    assert result.wake is True
    assert result.confidence_state == "unknown"
    assert result.wake_reason == "keyword"


def test_extract_vosk_confidence_averages_word_confidence():
    result = {"result": [{"word": "hey", "conf": 0.5}, {"word": "sarah", "conf": 0.7}]}
    assert abs((extract_vosk_confidence(result) or 0.0) - 0.6) < 0.001


def test_normalize_wake_text_collapses_noise():
    assert normalize_wake_text("  OK,   Sarah!!! ") == "ok sarah"
