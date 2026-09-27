# backend/tests/test_mood_system.py
"""
Tests for Mood System
=====================
Tests mood state, behavior dials, signal detection, and decay.
"""

import pytest
import tempfile
import sqlite3
from pathlib import Path
from datetime import datetime, timedelta

# Setup path for imports
import sys
backend_path = str(Path(__file__).parent.parent)
if backend_path not in sys.path:
    sys.path.insert(0, backend_path)

from mood.mood_state import (
    MoodState,
    Emotion,
    get_mood_state,
    save_mood_state,
    get_or_create_mood_state,
    init_mood_tables,
    _reset_mood_state,
)
from mood.mood_engine import (
    MoodEngine,
    BehaviorDials,
    EMOTION_BASELINES,
    AVATAR_PRESETS,
)
from mood.mood_signals import (
    MoodSignalDetector,
    MoodDecayManager,
    process_message_mood,
)


@pytest.fixture
def temp_db():
    """Create temporary database for testing."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = Path(f.name)

    yield db_path

    # Cleanup
    db_path.unlink(missing_ok=True)
    _reset_mood_state()


@pytest.fixture
def init_db(temp_db):
    """Initialize mood tables in temp database."""
    # Mock the get_connection to use temp db
    import mood.mood_state as mood_module

    original_get_connection = None
    if hasattr(mood_module, '_original_get_connection'):
        original_get_connection = mood_module._original_get_connection
    else:
        # Store original for restoration
        try:
            from backend.db import get_connection as orig_conn
            original_get_connection = orig_conn
            mood_module._original_get_connection = orig_conn
        except ImportError:
            pass

    # Create connection function for temp db
    def temp_get_connection():
        return sqlite3.connect(temp_db)

    # Patch the module
    mood_module.get_connection = temp_get_connection

    # Initialize tables
    init_mood_tables()

    yield temp_db

    # Restore original
    if original_get_connection:
        mood_module.get_connection = original_get_connection


# ============================================================================
# MOOD STATE TESTS
# ============================================================================

class TestMoodState:
    """Test MoodState dataclass."""

    def test_default_values(self):
        """MoodState should have sensible defaults."""
        state = MoodState(conversation_id=1)
        assert state.emotion == Emotion.NEUTRAL
        assert state.intensity == 0.2
        assert state.affinity == 0.9
        assert state.manual_override == False

    def test_set_emotion(self):
        """set_emotion should update emotion and intensity."""
        state = MoodState(conversation_id=1)
        state.set_emotion(Emotion.HAPPY, intensity=0.7)

        assert state.emotion == Emotion.HAPPY
        assert state.intensity == 0.7
        assert state.updated_at != ""

    def test_nudge_intensity_up(self):
        """nudge_intensity should increase within bounds."""
        state = MoodState(conversation_id=1, intensity=0.5)
        state.nudge_intensity(0.3)

        assert state.intensity == 0.8

    def test_nudge_intensity_clamps(self):
        """nudge_intensity should clamp to [0, 1]."""
        state = MoodState(conversation_id=1, intensity=0.9)
        state.nudge_intensity(0.5)

        assert state.intensity == 1.0

    def test_nudge_affinity_up(self):
        """nudge_affinity should increase within bounds."""
        state = MoodState(conversation_id=1, affinity=0.5)
        state.nudge_affinity(0.2)

        assert state.affinity == 0.7

    def test_nudge_affinity_down(self):
        """nudge_affinity should decrease within bounds."""
        state = MoodState(conversation_id=1, affinity=0.5)
        state.nudge_affinity(-0.3)

        assert state.affinity == 0.2

    def test_apply_decay(self):
        """apply_decay should reduce intensity toward baseline."""
        state = MoodState(conversation_id=1, emotion=Emotion.ANGRY, intensity=0.8)
        state.apply_decay(0.1)

        assert state.intensity < 0.8
        assert state.emotion == Emotion.ANGRY  # Still angry

    def test_apply_decay_resets_to_neutral(self):
        """apply_decay should reset to neutral when intensity is very low."""
        state = MoodState(conversation_id=1, emotion=Emotion.ANGRY, intensity=0.15)
        state.apply_decay(0.1)

        # Should reset to neutral when intensity drops below threshold
        assert state.emotion == Emotion.NEUTRAL

    def test_to_dict_and_from_dict(self):
        """MoodState should round-trip through dict."""
        original = MoodState(
            conversation_id=42,
            emotion=Emotion.EXCITED,
            intensity=0.85,
            affinity=0.75,
            manual_override=True,
        )

        data = original.to_dict()
        restored = MoodState.from_dict(data)

        assert restored.conversation_id == original.conversation_id
        assert restored.emotion == original.emotion
        assert restored.intensity == original.intensity
        assert restored.affinity == original.affinity
        assert restored.manual_override == original.manual_override


# ============================================================================
# BEHAVIOR DIALS TESTS
# ============================================================================

class TestBehaviorDials:
    """Test BehaviorDials dataclass and blending."""

    def test_default_values(self):
        """BehaviorDials should have neutral defaults."""
        dials = BehaviorDials()
        assert dials.warmth == 0.5
        assert dials.formality == 0.3
        assert dials.verbosity == 0.5

    def test_from_dict(self):
        """BehaviorDials should load from dict."""
        data = {"warmth": 0.9, "playfulness": 0.8}
        dials = BehaviorDials.from_dict(data)

        assert dials.warmth == 0.9
        assert dials.playfulness == 0.8
        assert dials.formality == 0.3  # Default

    def test_to_dict(self):
        """BehaviorDials should serialize to dict."""
        dials = BehaviorDials(warmth=0.8, empathy=0.9)
        data = dials.to_dict()

        assert data["warmth"] == 0.8
        assert data["empathy"] == 0.9

    def test_blend(self):
        """blend should interpolate between two dial sets."""
        base = BehaviorDials(warmth=0.2, empathy=0.2)
        target = BehaviorDials(warmth=1.0, empathy=1.0)

        blended = BehaviorDials.blend(base, target, 0.5)

        assert blended.warmth == pytest.approx(0.6, rel=0.01)
        assert blended.empathy == pytest.approx(0.6, rel=0.01)

    def test_blend_zero_factor(self):
        """blend with factor=0 should return base."""
        base = BehaviorDials(warmth=0.3)
        target = BehaviorDials(warmth=0.9)

        blended = BehaviorDials.blend(base, target, 0.0)

        assert blended.warmth == 0.3

    def test_blend_one_factor(self):
        """blend with factor=1 should return target."""
        base = BehaviorDials(warmth=0.3)
        target = BehaviorDials(warmth=0.9)

        blended = BehaviorDials.blend(base, target, 1.0)

        assert blended.warmth == 0.9


class TestMoodEngine:
    """Test MoodEngine behavior dial computation."""

    def test_compute_dials_neutral(self):
        """Neutral emotion should return near-neutral dials."""
        engine = MoodEngine()
        mood = MoodState(conversation_id=1, emotion=Emotion.NEUTRAL, intensity=0.2)

        dials = engine.compute_dials(mood)

        # Should be close to neutral baseline
        assert dials.warmth == pytest.approx(0.5, abs=0.15)

    def test_compute_dials_happy_high_intensity(self):
        """Happy with high intensity should have high warmth."""
        engine = MoodEngine()
        mood = MoodState(conversation_id=1, emotion=Emotion.HAPPY, intensity=0.9)

        dials = engine.compute_dials(mood)

        # Happy baseline has high warmth, blended at 0.9
        assert dials.warmth > 0.7
        assert dials.playfulness > 0.5

    def test_compute_dials_angry_high_intensity(self):
        """Angry with high intensity should have low warmth, high directness."""
        engine = MoodEngine()
        mood = MoodState(conversation_id=1, emotion=Emotion.ANGRY, intensity=0.9)

        dials = engine.compute_dials(mood)

        assert dials.warmth < 0.4
        assert dials.directness > 0.7

    def test_compute_dials_high_affinity_modifier(self):
        """High affinity should increase warmth and initiative."""
        engine = MoodEngine()
        mood_low = MoodState(conversation_id=1, emotion=Emotion.NEUTRAL, intensity=0.3, affinity=0.3)
        mood_high = MoodState(conversation_id=1, emotion=Emotion.NEUTRAL, intensity=0.3, affinity=0.9)

        dials_low = engine.compute_dials(mood_low)
        dials_high = engine.compute_dials(mood_high)

        # High affinity should increase warmth
        assert dials_high.warmth > dials_low.warmth

    def test_generate_style_instruction_not_empty(self):
        """generate_style_instruction should produce non-empty text."""
        engine = MoodEngine()
        dials = BehaviorDials(warmth=0.9, playfulness=0.8)

        instruction = engine.generate_style_instruction(dials)

        assert len(instruction) > 20
        assert "warm" in instruction.lower() or "caring" in instruction.lower()

    def test_get_full_style_injection(self):
        """get_full_style_injection should return complete block."""
        engine = MoodEngine()
        mood = MoodState(conversation_id=1, emotion=Emotion.HAPPY, intensity=0.7)

        injection = engine.get_full_style_injection(mood)

        assert "[STYLE GUIDANCE" in injection
        assert "DO NOT MENTION" in injection

    def test_get_avatar_parameters(self):
        """get_avatar_parameters should return semantic avatar params."""
        engine = MoodEngine()
        mood = MoodState(conversation_id=1, emotion=Emotion.HAPPY, intensity=0.8)

        params = engine.get_avatar_parameters(mood)

        assert params["preset_name"] == "happy"
        assert params["emotion"] == "happy"
        assert "parameters" in params
        assert params["parameters"]["eye_smile"] > 0
        assert params["parameters"]["mouth_smile"] > 0


# ============================================================================
# MOOD SIGNAL DETECTION TESTS
# ============================================================================

class TestMoodSignalDetector:
    """Test mood signal detection from messages."""

    def test_detect_frustration(self):
        """Should detect frustration signals."""
        detector = MoodSignalDetector()

        signals = detector.detect_signals("This is not working!!")

        assert signals["frustration"] > 0.2

    def test_detect_success(self):
        """Should detect success signals."""
        detector = MoodSignalDetector()

        signals = detector.detect_signals("Thanks, that works perfectly!")

        assert signals["success"] > 0.2

    def test_detect_confusion(self):
        """Should detect confusion signals."""
        detector = MoodSignalDetector()

        signals = detector.detect_signals("Wait, I don't understand what you mean")

        assert signals["confusion"] > 0.2

    def test_detect_positive_bond(self):
        """Should detect positive bond signals."""
        detector = MoodSignalDetector()

        signals = detector.detect_signals("You're the best, I appreciate your help!")

        assert signals["positive_bond"] > 0.2

    def test_detect_negative_bond(self):
        """Should detect negative bond signals."""
        detector = MoodSignalDetector()

        signals = detector.detect_signals("This is useless, shut up")

        assert signals["negative_bond"] > 0.2

    def test_no_signals_neutral_message(self):
        """Neutral message should have low signals."""
        detector = MoodSignalDetector()

        signals = detector.detect_signals("Can you help me with my project?")

        # All signals should be low
        assert all(v < 0.2 for v in signals.values())

    def test_apply_signals_updates_mood(self):
        """apply_signals should update mood based on detected signals."""
        detector = MoodSignalDetector()
        mood = MoodState(conversation_id=1, emotion=Emotion.NEUTRAL, intensity=0.2)

        updated_mood, changes = detector.apply_signals(
            mood, "NOT WORKING!! This is broken!", save=False
        )

        assert updated_mood.emotion == Emotion.FRUSTRATED
        assert "emotion" in changes

    def test_apply_signals_respects_manual_override(self):
        """apply_signals should not change mood if manual_override is True."""
        detector = MoodSignalDetector()
        mood = MoodState(
            conversation_id=1,
            emotion=Emotion.HAPPY,
            intensity=0.8,
            manual_override=True
        )

        updated_mood, changes = detector.apply_signals(
            mood, "NOT WORKING!! This is terrible!", save=False
        )

        # Should not change due to manual override
        assert updated_mood.emotion == Emotion.HAPPY
        assert "skipped" in changes


class TestMoodDecayManager:
    """Test mood decay over time."""

    def test_should_decay_returns_false_for_recent(self):
        """should_decay should return False for recently updated mood."""
        manager = MoodDecayManager()
        mood = MoodState(conversation_id=1, emotion=Emotion.ANGRY, intensity=0.8)
        mood.updated_at = datetime.utcnow().isoformat()

        assert manager.should_decay(mood) == False

    def test_should_decay_returns_true_for_old(self):
        """should_decay should return True for old mood."""
        manager = MoodDecayManager()
        mood = MoodState(conversation_id=1, emotion=Emotion.ANGRY, intensity=0.8)
        # Set updated_at to 10 minutes ago
        old_time = datetime.utcnow() - timedelta(minutes=10)
        mood.updated_at = old_time.isoformat()

        assert manager.should_decay(mood) == True

    def test_should_decay_respects_manual_override(self):
        """should_decay should return False if manual_override is True."""
        manager = MoodDecayManager()
        mood = MoodState(
            conversation_id=1,
            emotion=Emotion.ANGRY,
            intensity=0.8,
            manual_override=True
        )
        old_time = datetime.utcnow() - timedelta(minutes=10)
        mood.updated_at = old_time.isoformat()

        assert manager.should_decay(mood) == False

    def test_apply_decay_reduces_intensity(self):
        """apply_decay should reduce mood intensity."""
        manager = MoodDecayManager()
        mood = MoodState(conversation_id=1, emotion=Emotion.ANGRY, intensity=0.8)
        old_time = datetime.utcnow() - timedelta(minutes=10)
        mood.updated_at = old_time.isoformat()

        decayed = manager.apply_decay(mood, save=False)

        assert decayed.intensity < 0.8


class TestProcessMessageMood:
    """Test the convenience function for processing messages."""

    def test_process_positive_message(self):
        """Positive message should make mood happier."""
        mood = MoodState(conversation_id=1, emotion=Emotion.NEUTRAL, intensity=0.2)

        updated, changes = process_message_mood(mood, "Thanks! That's perfect!")

        assert updated.emotion == Emotion.HAPPY

    def test_process_negative_message(self):
        """Negative message should affect mood."""
        mood = MoodState(conversation_id=1, emotion=Emotion.NEUTRAL, intensity=0.2)

        updated, changes = process_message_mood(mood, "This is still not working, wtf!")

        assert updated.emotion == Emotion.FRUSTRATED


# ============================================================================
# EMOTION BASELINES TESTS
# ============================================================================

class TestEmotionBaselines:
    """Test that emotion baselines are properly defined."""

    def test_all_emotions_have_baselines(self):
        """Every Emotion should have a baseline."""
        for emotion in Emotion:
            assert emotion in EMOTION_BASELINES, f"Missing baseline for {emotion}"

    def test_baselines_are_valid_dials(self):
        """All baselines should be valid BehaviorDials."""
        for emotion, baseline in EMOTION_BASELINES.items():
            assert isinstance(baseline, BehaviorDials)
            assert 0 <= baseline.warmth <= 1
            assert 0 <= baseline.formality <= 1


class TestAvatarPresets:
    """Test avatar parameter presets."""

    def test_all_emotions_have_presets(self):
        """Every Emotion should have avatar presets."""
        for emotion in Emotion:
            assert emotion in AVATAR_PRESETS, f"Missing avatar preset for {emotion}"

    def test_presets_have_required_params(self):
        """Presets should have common semantic avatar parameters."""
        required = {"eye_smile", "cheek_blush", "brow_angle", "mouth_smile", "body_tilt"}
        for emotion, params in AVATAR_PRESETS.items():
            assert params.get("preset_name"), f"{emotion} preset missing name"
            assert required.issubset(params), f"{emotion} preset missing expected parameters"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
