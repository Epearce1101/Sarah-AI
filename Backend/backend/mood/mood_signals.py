# backend/mood/mood_signals.py
"""
Mood Signals
============
Automatic mood adjustments based on conversation signals.

Detects patterns in user messages and adjusts mood accordingly:
- Frustration signals: errors, caps, "not working", repeated attempts
- Success signals: "thanks", "perfect", "works now"
- Confusion signals: conflicting instructions, vague requests
- Positive signals: compliments, gratitude

All adjustments respect manual_override flag.
"""

import re
from datetime import datetime, timedelta
from typing import Optional, Tuple
from .mood_state import MoodState, Emotion, save_mood_state


class MoodSignalDetector:
    """
    Detects mood signals from user messages and updates mood state.
    """

    # Frustration patterns
    FRUSTRATION_PATTERNS = [
        r"\b(not working|doesn't work|won't work|broken|failed)\b",
        r"\b(still not|still broken|still failing|tried everything)\b",
        r"\b(what the|wtf|ugh|argh|damn|dammit)\b",
        r"\b(this is ridiculous|so annoying|frustrated)\b",
        r"\?{2,}|\?!|!\?",  # "??", "?!" read as exasperation ("!!" alone is usually excitement)
        r"^[A-Z\s]{10,}$",  # All caps messages (shouting)
    ]

    # Success patterns
    SUCCESS_PATTERNS = [
        r"\b(thank|thanks|thx|ty)\b",
        r"\b(perfect|excellent|great|awesome|amazing)\b",
        r"(?<!not )(?<!n't )(?<!never )\b(works|working|it works|that works|fixed)\b",
        r"\b(nice|love it|beautiful|brilliant)\b",
        r"\b(exactly what i needed|just what i wanted)\b",
    ]

    # Confusion patterns
    CONFUSION_PATTERNS = [
        r"\b(wait|actually|no i meant|sorry i mean)\b",
        r"\b(i don't understand|what do you mean|huh)\b",
        r"\b(let me clarify|to clarify|actually no)\b",
        r"\b(confused|confusing|unclear)\b",
    ]

    # Positive/bond patterns (increase affinity)
    POSITIVE_PATTERNS = [
        r"\b(you're (the )?best|love you|good girl)\b",
        r"\b(i appreciate|really helpful|so helpful)\b",
        r"\b(missed you|glad you're here)\b",
    ]

    # Negative/distance patterns (decrease affinity)
    NEGATIVE_PATTERNS = [
        r"\b(useless|worthless|terrible|horrible)\b",
        r"\b(you're (so )?bad|you suck|hate this)\b",
        r"\b(shut up|go away|leave me alone)\b",
    ]

    def __init__(self):
        # Compile patterns for efficiency
        self._frustration_re = [re.compile(p, re.IGNORECASE) for p in self.FRUSTRATION_PATTERNS]
        self._success_re = [re.compile(p, re.IGNORECASE) for p in self.SUCCESS_PATTERNS]
        self._confusion_re = [re.compile(p, re.IGNORECASE) for p in self.CONFUSION_PATTERNS]
        self._positive_re = [re.compile(p, re.IGNORECASE) for p in self.POSITIVE_PATTERNS]
        self._negative_re = [re.compile(p, re.IGNORECASE) for p in self.NEGATIVE_PATTERNS]

    def _count_matches(self, text: str, patterns: list) -> int:
        """Count number of pattern matches in text."""
        count = 0
        for pattern in patterns:
            if pattern.search(text):
                count += 1
        return count

    def detect_signals(self, message: str) -> dict:
        """
        Detect mood signals in a message.

        Returns dict with signal strengths (0.0-1.0):
        - frustration
        - success
        - confusion
        - positive_bond
        - negative_bond
        """
        message = message.strip()

        # Count matches
        frustration = self._count_matches(message, self._frustration_re)
        success = self._count_matches(message, self._success_re)
        confusion = self._count_matches(message, self._confusion_re)
        positive = self._count_matches(message, self._positive_re)
        negative = self._count_matches(message, self._negative_re)

        # Normalize to 0-1 (cap at 1.0)
        return {
            "frustration": min(1.0, frustration * 0.3),
            "success": min(1.0, success * 0.4),
            "confusion": min(1.0, confusion * 0.35),
            "positive_bond": min(1.0, positive * 0.5),
            "negative_bond": min(1.0, negative * 0.4),
        }

    def apply_signals(
        self,
        mood: MoodState,
        message: str,
        save: bool = True,
    ) -> Tuple[MoodState, dict]:
        """
        Apply detected signals to mood state.

        Returns:
        - Updated mood state
        - Dict of applied changes for logging

        Respects manual_override - won't change emotion if set manually.
        """
        if mood.manual_override:
            return mood, {"skipped": "manual_override"}

        signals = self.detect_signals(message)
        changes = {}

        # Determine dominant signal
        max_signal = max(signals.values())
        if max_signal < 0.1:
            return mood, {"no_signal": True}

        # Apply emotion changes based on dominant signal
        dominant = max(signals.items(), key=lambda x: x[1])
        signal_name, strength = dominant

        if signal_name == "frustration" and strength > 0.2:
            if mood.emotion == Emotion.FRUSTRATED:
                # Already frustrated, increase intensity
                mood.nudge_intensity(strength * 0.2)
                mood.trigger_count += 1
            else:
                # Become frustrated
                mood.set_emotion(Emotion.FRUSTRATED, intensity=0.3 + strength * 0.3)
            changes["emotion"] = "frustrated"
            changes["reason"] = "frustration_detected"

        elif signal_name == "success" and strength > 0.2:
            mood.set_emotion(Emotion.HAPPY, intensity=0.3 + strength * 0.4)
            changes["emotion"] = "happy"
            changes["reason"] = "success_detected"

        elif signal_name == "confusion" and strength > 0.2:
            mood.set_emotion(Emotion.CONFUSED, intensity=0.3 + strength * 0.3)
            changes["emotion"] = "confused"
            changes["reason"] = "confusion_detected"

        # Apply affinity changes
        if signals["positive_bond"] > 0.2:
            mood.nudge_affinity(signals["positive_bond"] * 0.1)
            changes["affinity"] = f"+{signals['positive_bond'] * 0.1:.2f}"

        if signals["negative_bond"] > 0.2:
            mood.nudge_affinity(-signals["negative_bond"] * 0.15)
            changes["affinity"] = f"-{signals['negative_bond'] * 0.15:.2f}"

        if save and changes:
            save_mood_state(mood)

        return mood, changes


class MoodDecayManager:
    """
    Manages mood decay over time.

    - Intensity decays toward 0.2 baseline
    - Emotion returns to NEUTRAL when intensity is very low
    """

    DECAY_INTERVAL_SECONDS = 300  # 5 minutes
    INTENSITY_DECAY_RATE = 0.05   # Per interval

    def should_decay(self, mood: MoodState) -> bool:
        """Check if enough time has passed for decay."""
        if mood.manual_override:
            return False

        if not mood.updated_at:
            return False

        try:
            last_update = datetime.fromisoformat(mood.updated_at)
            elapsed = (datetime.utcnow() - last_update).total_seconds()
            return elapsed >= self.DECAY_INTERVAL_SECONDS
        except (ValueError, TypeError):
            return False

    def apply_decay(self, mood: MoodState, save: bool = True) -> MoodState:
        """Apply natural decay to mood state."""
        if not self.should_decay(mood):
            return mood

        mood.apply_decay(self.INTENSITY_DECAY_RATE)

        if save:
            save_mood_state(mood)

        return mood


# ============================================================
# SINGLETON INSTANCES
# ============================================================

_signal_detector: Optional[MoodSignalDetector] = None
_decay_manager: Optional[MoodDecayManager] = None


def get_signal_detector() -> MoodSignalDetector:
    """Get or create global signal detector."""
    global _signal_detector
    if _signal_detector is None:
        _signal_detector = MoodSignalDetector()
    return _signal_detector


def get_decay_manager() -> MoodDecayManager:
    """Get or create global decay manager."""
    global _decay_manager
    if _decay_manager is None:
        _decay_manager = MoodDecayManager()
    return _decay_manager


_PERCEIVED = {
    "frustration": "frustrated",
    "success": "pleased",
    "confusion": "confused",
    "positive_bond": "affectionate toward you",
    "negative_bond": "upset with you",
}


def perceive_user_message(mood: MoodState, user_message: str) -> Tuple[MoodState, Optional[str]]:
    """How the user seems, from their message, without deciding how Sarah
    feels. Her own feeling comes from her reply (<feel>); what she reads in
    the user only colours it (and nudges the bond/affinity).

    Returns the (decayed, affinity-nudged, saved) mood and a short
    description of the user's state, or None if nothing stood out.
    """
    decay_mgr = get_decay_manager()
    if decay_mgr.should_decay(mood):
        mood = decay_mgr.apply_decay(mood, save=False)

    signals = get_signal_detector().detect_signals(user_message or "")
    changed = False
    if signals["positive_bond"] > 0.2:
        mood.nudge_affinity(signals["positive_bond"] * 0.1)
        changed = True
    if signals["negative_bond"] > 0.2:
        mood.nudge_affinity(-signals["negative_bond"] * 0.15)
        changed = True
    if changed:
        save_mood_state(mood)

    name, strength = max(signals.items(), key=lambda kv: kv[1])
    return mood, (_PERCEIVED[name] if strength > 0.2 else None)


def process_message_mood(
    mood: MoodState,
    user_message: str,
    apply_decay: bool = True,
) -> Tuple[MoodState, dict]:
    """
    Process a user message and update mood accordingly.

    Convenience function that:
    1. Applies decay if needed
    2. Detects and applies mood signals

    Returns:
    - Updated mood state
    - Dict with applied changes
    """
    changes = {}

    # Apply decay first
    if apply_decay:
        decay_mgr = get_decay_manager()
        if decay_mgr.should_decay(mood):
            mood = decay_mgr.apply_decay(mood, save=False)
            changes["decay_applied"] = True

    # Apply signals
    detector = get_signal_detector()
    mood, signal_changes = detector.apply_signals(mood, user_message, save=True)
    changes.update(signal_changes)

    return mood, changes
