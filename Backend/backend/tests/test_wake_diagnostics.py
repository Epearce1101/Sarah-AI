from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from backend.audio.wake_diagnostics import (
    configure_wake_diagnostics,
    get_wake_diagnostics,
    record_wake_transcript,
)
from backend.audio.wake_words import WakeMatchResult


def test_wake_diagnostics_records_reason_and_cooldown():
    configure_wake_diagnostics(listener_running=True)
    result = WakeMatchResult(
        wake=True,
        match="hey",
        match_type="prefix_fallback",
        reason="matched",
        wake_reason="prefix_fallback",
        confidence_state="unknown",
        confidence=None,
        prefix_hit_count=2,
    )

    record_wake_transcript(
        text="hey",
        source="final",
        result=result,
        matcher_last_wake_at=100.0,
        event_pending=True,
        now_monotonic=100.0,
    )

    snapshot = get_wake_diagnostics(event_pending=True, listener_running=True)

    assert snapshot["ok"] is True
    assert snapshot["listener_running"] is True
    assert snapshot["event_pending"] is True
    assert snapshot["last_transcript"] == "hey"
    assert snapshot["confidence_state"] == "unknown"
    assert snapshot["reason"] == "matched"
    assert snapshot["wake_reason"] == "prefix_fallback"
    assert snapshot["match_type"] == "prefix_fallback"
    assert snapshot["current_state"] == "awake"
    assert snapshot["recent_transcripts"][-1]["text"] == "hey"
