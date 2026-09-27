# backend/mood/mood_state.py
"""
Mood State Model
================
Single source of truth for Sarah's emotional state.

Shared by:
- Chat engine (LLM request builder)
- Avatar renderer/animator (UI)

Fields:
- emotion: HAPPY, EXCITED, CONFUSED, ANGRY, SAD, NEUTRAL, FRUSTRATED
- intensity: 0.0-1.0 (how strongly the emotion is felt)
- affinity: 0.0-1.0 (closeness/bond with creator)
- updated_at: timestamp
- manual_override: if user set mood, don't auto-change
"""

import json
import sqlite3
from datetime import datetime
from enum import Enum
from dataclasses import dataclass, asdict
from typing import Optional
from pathlib import Path


class Emotion(str, Enum):
    """Valid emotion modes for Sarah."""
    HAPPY = "happy"
    EXCITED = "excited"
    CONFUSED = "confused"
    ANGRY = "angry"
    SAD = "sad"
    NEUTRAL = "neutral"
    FRUSTRATED = "frustrated"

    @classmethod
    def from_string(cls, value: str) -> "Emotion":
        """Parse emotion from string, defaulting to NEUTRAL."""
        try:
            return cls(value.lower().strip())
        except (ValueError, AttributeError):
            return cls.NEUTRAL


@dataclass
class MoodState:
    """
    Complete mood state for a conversation.

    This is the single source of truth shared between:
    - Backend chat engine (for style injection)
    - Frontend avatar (for expression/animation)
    """
    conversation_id: int
    emotion: Emotion = Emotion.NEUTRAL
    intensity: float = 0.2  # 0.0-1.0, default subtle
    affinity: float = 0.9   # 0.0-1.0, default high (creator bond)
    updated_at: str = ""    # ISO timestamp
    manual_override: bool = False  # If True, don't auto-change emotion

    # Decay tracking
    last_trigger_at: str = ""  # When last mood trigger occurred
    trigger_count: int = 0     # Number of triggers in current emotion

    def __post_init__(self):
        # Ensure emotion is Emotion type
        if isinstance(self.emotion, str):
            self.emotion = Emotion.from_string(self.emotion)

        # Set updated_at if not set
        if not self.updated_at:
            self.updated_at = datetime.utcnow().isoformat()

        # Clamp values
        self.intensity = max(0.0, min(1.0, float(self.intensity)))
        self.affinity = max(0.0, min(1.0, float(self.affinity)))

    def to_dict(self) -> dict:
        """Serialize to dictionary for storage/API."""
        return {
            "conversation_id": self.conversation_id,
            "emotion": self.emotion.value,
            "intensity": self.intensity,
            "affinity": self.affinity,
            "updated_at": self.updated_at,
            "manual_override": self.manual_override,
            "last_trigger_at": self.last_trigger_at,
            "trigger_count": self.trigger_count,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "MoodState":
        """Deserialize from dictionary."""
        return cls(
            conversation_id=data.get("conversation_id", 0),
            emotion=Emotion.from_string(data.get("emotion", "neutral")),
            intensity=float(data.get("intensity", 0.2)),
            affinity=float(data.get("affinity", 0.9)),
            updated_at=data.get("updated_at", ""),
            manual_override=bool(data.get("manual_override", False)),
            last_trigger_at=data.get("last_trigger_at", ""),
            trigger_count=int(data.get("trigger_count", 0)),
        )

    def set_emotion(
        self,
        emotion: Emotion,
        intensity: Optional[float] = None,
        is_manual: bool = False,
    ):
        """
        Update emotion state.

        Args:
            emotion: New emotion
            intensity: Optional intensity (keeps current if None)
            is_manual: If True, sets manual_override flag
        """
        self.emotion = emotion
        if intensity is not None:
            self.intensity = max(0.0, min(1.0, intensity))
        self.updated_at = datetime.utcnow().isoformat()
        self.last_trigger_at = self.updated_at
        self.trigger_count = 1

        if is_manual:
            self.manual_override = True

    def nudge_intensity(self, delta: float):
        """Adjust intensity by delta, clamping to 0.0-1.0."""
        self.intensity = max(0.0, min(1.0, self.intensity + delta))
        self.updated_at = datetime.utcnow().isoformat()

    def nudge_affinity(self, delta: float):
        """Adjust affinity by delta, clamping to 0.0-1.0."""
        self.affinity = max(0.0, min(1.0, self.affinity + delta))
        self.updated_at = datetime.utcnow().isoformat()

    def unlock_override(self):
        """Allow automatic mood changes again."""
        self.manual_override = False
        self.updated_at = datetime.utcnow().isoformat()

    def apply_decay(self, decay_rate: float = 0.1):
        """
        Apply natural decay toward neutral state.

        - Intensity decays toward 0.2 (baseline)
        - Emotion decays to NEUTRAL when intensity is very low
        """
        if self.manual_override:
            return  # Don't decay if manually set

        # Decay intensity toward baseline (0.2)
        baseline = 0.2
        if self.intensity > baseline:
            self.intensity = max(baseline, self.intensity - decay_rate)
        elif self.intensity < baseline:
            self.intensity = min(baseline, self.intensity + decay_rate * 0.5)

        # If intensity is very low and not neutral, drift to neutral
        if self.intensity <= baseline + 0.05 and self.emotion != Emotion.NEUTRAL:
            self.emotion = Emotion.NEUTRAL

        self.updated_at = datetime.utcnow().isoformat()


# ============================================================
# DATABASE STORAGE
# ============================================================

def _get_db_path() -> Path:
    """Get database path from main app."""
    try:
        from backend.db import DB_PATH
        return DB_PATH
    except ImportError:
        # Fallback
        return Path(__file__).parent.parent / "data" / "sarah.db"


def _ensure_mood_table():
    """Create mood_state table if it doesn't exist."""
    db_path = _get_db_path()
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS mood_state (
            conversation_id INTEGER PRIMARY KEY,
            mood_json TEXT NOT NULL,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
    """)

    conn.commit()
    conn.close()


def get_mood_state(conversation_id: int) -> Optional[MoodState]:
    """
    Get mood state for a conversation.
    Returns None if not found.
    """
    _ensure_mood_table()
    db_path = _get_db_path()

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    cur.execute(
        "SELECT mood_json FROM mood_state WHERE conversation_id = ?",
        (conversation_id,)
    )
    row = cur.fetchone()
    conn.close()

    if row and row["mood_json"]:
        try:
            data = json.loads(row["mood_json"])
            data["conversation_id"] = conversation_id
            return MoodState.from_dict(data)
        except (json.JSONDecodeError, KeyError):
            pass

    return None


def save_mood_state(mood: MoodState):
    """Save or update mood state in database."""
    _ensure_mood_table()
    db_path = _get_db_path()

    mood.updated_at = datetime.utcnow().isoformat()

    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    cur.execute("""
        INSERT INTO mood_state (conversation_id, mood_json, updated_at)
        VALUES (?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(conversation_id) DO UPDATE SET
            mood_json = excluded.mood_json,
            updated_at = CURRENT_TIMESTAMP;
    """, (mood.conversation_id, json.dumps(mood.to_dict())))

    conn.commit()
    conn.close()


def get_or_create_mood_state(conversation_id: int) -> MoodState:
    """
    Get existing mood state or create a new one with defaults.
    """
    existing = get_mood_state(conversation_id)
    if existing:
        return existing

    # Create new with defaults
    mood = MoodState(
        conversation_id=conversation_id,
        emotion=Emotion.NEUTRAL,
        intensity=0.2,
        affinity=0.9,  # High affinity by default (creator bond)
    )
    save_mood_state(mood)
    return mood


def delete_mood_state(conversation_id: int):
    """Delete mood state for a conversation."""
    _ensure_mood_table()
    db_path = _get_db_path()

    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute(
        "DELETE FROM mood_state WHERE conversation_id = ?",
        (conversation_id,)
    )
    conn.commit()
    conn.close()


# ============================================================
# TEST HELPERS
# ============================================================

# Allow external connection injection for testing
_test_connection = None


def get_connection():
    """Get database connection (supports test injection)."""
    global _test_connection
    if _test_connection is not None:
        return _test_connection()
    return sqlite3.connect(_get_db_path())


def init_mood_tables():
    """Initialize mood tables (for testing)."""
    _ensure_mood_table()


def _reset_mood_state():
    """Reset global state (for testing)."""
    global _test_connection
    _test_connection = None