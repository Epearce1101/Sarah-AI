"""Sarah's embodiment: one self shared by the language model and the avatar.

- ``self_model``: her feeling, body state and senses; ``render_now`` tells
  every prompt what this moment is like for her.
- ``impulse``: events that happen to her body, and her spoken reactions.
"""
from .self_model import (
    Feeling,
    SelfModel,
    feeling_from_reply,
    get_self,
    is_silent,
    mood_emotion_for,
    parse_feel,
    strip_body_tags,
)

__all__ = [
    "Feeling",
    "SelfModel",
    "feeling_from_reply",
    "get_self",
    "is_silent",
    "mood_emotion_for",
    "parse_feel",
    "strip_body_tags",
]
