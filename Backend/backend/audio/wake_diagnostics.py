"""In-memory wake-word diagnostics snapshot."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from threading import Lock
import time
from typing import Any

from backend.config import settings
from backend.audio.wake_words import WakeMatchResult


@dataclass
class WakeTranscriptEntry:
    text: str
    source: str
    confidence: float | None
    confidence_state: str
    reason: str
    wake_reason: str
    match: str | None
    match_type: str
    woke: bool
    timestamp: float


@dataclass
class WakeDiagnosticsState:
    listener_running: bool = False
    event_pending: bool = False
    current_state: str = "offline"
    last_transcript: str = ""
    last_source: str = ""
    last_confidence: float | None = None
    confidence_state: str = "unknown"
    reason: str = "not_started"
    wake_reason: str = "not_started"
    matched_phrase: str | None = None
    match_type: str = "none"
    prefix_hit_count: int = 0
    prefix_hits_required: int = 2
    cooldown_seconds: float = 2.0
    cooldown_remaining_seconds: float = 0.0
    min_confidence: float = 0.35
    single_prefix_confidence: float = 0.50
    prefix_fallback_enabled: bool = True
    prefix_fallback_final_only: bool = True
    last_wake_at: float | None = None
    updated_at: float | None = None
    recent_transcripts: list[WakeTranscriptEntry] = field(default_factory=list)


_LOCK = Lock()
_STATE = WakeDiagnosticsState()
_MAX_RECENT = 10


def _current_state_for(result: WakeMatchResult, source: str) -> str:
    if result.wake:
        return "awake"
    if result.reason == "prefix_pending":
        return "prefix_pending"
    if result.reason in {"cooldown", "low_confidence"}:
        return "blocked"
    if source == "partial":
        return "listening"
    return "idle"


def configure_wake_diagnostics(listener_running: bool) -> None:
    """Record static wake listener settings and liveness."""
    with _LOCK:
        _STATE.listener_running = listener_running
        _STATE.current_state = "listening" if listener_running else "offline"
        _STATE.prefix_hits_required = max(1, settings.wake_prefix_fallback_hits)
        _STATE.cooldown_seconds = max(0.0, settings.wake_cooldown_seconds)
        _STATE.min_confidence = max(0.0, settings.wake_min_confidence)
        _STATE.single_prefix_confidence = max(0.0, settings.wake_prefix_single_confidence)
        _STATE.prefix_fallback_enabled = settings.wake_prefix_fallback_enabled
        _STATE.prefix_fallback_final_only = True
        _STATE.updated_at = time.time()


def record_wake_transcript(
    *,
    text: str,
    source: str,
    result: WakeMatchResult,
    matcher_last_wake_at: float,
    event_pending: bool,
    now_monotonic: float,
) -> None:
    """Record the latest transcript and matcher outcome for diagnostics."""
    timestamp = time.time()
    cooldown = max(0.0, settings.wake_cooldown_seconds - (now_monotonic - matcher_last_wake_at))
    entry = WakeTranscriptEntry(
        text=text,
        source=source,
        confidence=result.confidence,
        confidence_state=result.confidence_state,
        reason=result.reason,
        wake_reason=result.wake_reason,
        match=result.match,
        match_type=result.match_type,
        woke=result.wake,
        timestamp=timestamp,
    )

    with _LOCK:
        _STATE.listener_running = True
        _STATE.event_pending = event_pending
        _STATE.current_state = _current_state_for(result, source)
        _STATE.last_transcript = text
        _STATE.last_source = source
        _STATE.last_confidence = result.confidence
        _STATE.confidence_state = result.confidence_state
        _STATE.reason = result.reason
        _STATE.wake_reason = result.wake_reason
        _STATE.matched_phrase = result.match
        _STATE.match_type = result.match_type
        _STATE.prefix_hit_count = result.prefix_hit_count
        _STATE.prefix_hits_required = max(1, settings.wake_prefix_fallback_hits)
        _STATE.cooldown_seconds = max(0.0, settings.wake_cooldown_seconds)
        _STATE.cooldown_remaining_seconds = cooldown
        _STATE.min_confidence = max(0.0, settings.wake_min_confidence)
        _STATE.single_prefix_confidence = max(0.0, settings.wake_prefix_single_confidence)
        _STATE.prefix_fallback_enabled = settings.wake_prefix_fallback_enabled
        _STATE.prefix_fallback_final_only = True
        _STATE.last_wake_at = time.time() if result.wake else _STATE.last_wake_at
        _STATE.updated_at = timestamp
        _STATE.recent_transcripts.append(entry)
        del _STATE.recent_transcripts[:-_MAX_RECENT]


def set_wake_event_pending(event_pending: bool) -> None:
    with _LOCK:
        _STATE.event_pending = event_pending
        _STATE.updated_at = time.time()


def set_wake_listener_error(error: str) -> None:
    with _LOCK:
        _STATE.listener_running = False
        _STATE.current_state = "error"
        _STATE.reason = "listener_error"
        _STATE.wake_reason = "listener_error"
        _STATE.last_transcript = error
        _STATE.updated_at = time.time()


def get_wake_diagnostics(event_pending: bool = False, listener_running: bool | None = None) -> dict[str, Any]:
    """Return a JSON-serializable diagnostics snapshot."""
    with _LOCK:
        if listener_running is not None:
            _STATE.listener_running = listener_running
            if not listener_running and _STATE.current_state != "error":
                _STATE.current_state = "offline"
        _STATE.event_pending = event_pending
        if _STATE.last_wake_at is not None:
            elapsed = time.time() - _STATE.last_wake_at
            _STATE.cooldown_remaining_seconds = max(0.0, settings.wake_cooldown_seconds - elapsed)
        data = asdict(_STATE)

    data["ok"] = True
    return data
