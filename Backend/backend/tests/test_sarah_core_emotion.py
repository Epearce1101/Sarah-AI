"""Runtime tests for SarahCore._derive_emotion (B5 engine reroute).

The legacy `EmotionalEngine` was deleted in B5; emotion now comes from
`backend.mood.MoodState`. `_derive_emotion` is the single bridge between
the new engine and `SarahReply.emotion`. It has a silent `except Exception`
that masks failures, so this module exercises every branch directly.

We call `_derive_emotion` as an unbound method against a stub `self`,
avoiding the full SarahCore constructor (LLMClient, MultiAgentBrain, etc.).
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from backend.sarah_core import SarahCore
from backend.mood.mood_state import MoodState, Emotion


def _call(conversation_id):
    """Invoke `_derive_emotion` against a stub `self` (skips SarahCore.__init__)."""
    stub = SimpleNamespace()
    return SarahCore._derive_emotion(stub, conversation_id)


def _mood(emotion: Emotion, intensity: float, conv_id: int = 1) -> MoodState:
    return MoodState(conversation_id=conv_id, emotion=emotion, intensity=intensity)


class TestNoConversation:
    def test_none_conversation_id_returns_neutral(self):
        assert _call(None) == ("neutral", 0.0)


class TestHappyAffectionateMapping:
    """HAPPY+intensity≥0.7 → "affectionate" (Jessie persona affordance)."""

    def test_happy_high_intensity_maps_to_affectionate(self):
        with patch(
            "backend.mood.get_or_create_mood_state",
            return_value=_mood(Emotion.HAPPY, 0.8),
        ):
            label, intensity = _call(1)
        assert label == "affectionate"
        assert intensity == pytest.approx(0.8)

    def test_happy_at_threshold_maps_to_affectionate(self):
        """Boundary: 0.7 exactly is the threshold — must map."""
        with patch(
            "backend.mood.get_or_create_mood_state",
            return_value=_mood(Emotion.HAPPY, 0.7),
        ):
            label, _ = _call(1)
        assert label == "affectionate"

    def test_happy_below_threshold_stays_happy(self):
        with patch(
            "backend.mood.get_or_create_mood_state",
            return_value=_mood(Emotion.HAPPY, 0.6),
        ):
            label, intensity = _call(1)
        assert label == "happy"
        assert intensity == pytest.approx(0.6)


class TestNonHappyPassthrough:
    """All other emotions must pass through unchanged regardless of intensity."""

    @pytest.mark.parametrize("emotion", [
        Emotion.EXCITED,
        Emotion.SAD,
        Emotion.ANGRY,
        Emotion.CONFUSED,
        Emotion.FRUSTRATED,
        Emotion.NEUTRAL,
    ])
    def test_emotion_passes_through(self, emotion: Emotion):
        with patch(
            "backend.mood.get_or_create_mood_state",
            return_value=_mood(emotion, 0.9),
        ):
            label, intensity = _call(1)
        assert label == emotion.value
        assert intensity == pytest.approx(0.9)


class TestSilentExceptPath:
    """The `except Exception` swallows everything — verify it lands on neutral."""

    def test_mood_module_raise_returns_neutral(self):
        def boom(_conv_id):
            raise RuntimeError("mood DB unavailable")
        with patch("backend.mood.get_or_create_mood_state", side_effect=boom):
            assert _call(1) == ("neutral", 0.0)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
