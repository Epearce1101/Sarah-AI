# backend/mood/__init__.py
"""
Sarah AI Mood System
====================
Manages mood state and behavior dials for conversation style and avatar coupling.

All mood operations are INTERNAL and SILENT - never shown directly to user.
The mood state influences:
1. LLM chat output style (via behavior dial injection)
2. Avatar expression/animation (via UI coupling)
"""

from .mood_state import (
    MoodState,
    Emotion,
    get_mood_state,
    save_mood_state,
    get_or_create_mood_state,
)
from .mood_engine import (
    MoodEngine,
    BehaviorDials,
    get_mood_engine,
)

__all__ = [
    "MoodState",
    "Emotion",
    "get_mood_state",
    "save_mood_state",
    "get_or_create_mood_state",
    "MoodEngine",
    "BehaviorDials",
    "get_mood_engine",
]