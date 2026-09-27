# backend/memory/__init__.py
"""
Sarah AI Memory System
======================
Manages conversation context, summaries, and state for reliable long conversations.

All memory operations are SILENT - never shown to the user.
"""

from .config import MemoryConfig, get_memory_config
from .memory_store import MemoryStore, get_memory_store
from .summarizer import Summarizer
from .intent_resolver import IntentResolver, ResolvedIntent
from .context_builder import ContextBuilder, LLMContextPacket

__all__ = [
    "MemoryConfig",
    "get_memory_config",
    "MemoryStore",
    "get_memory_store",
    "Summarizer",
    "IntentResolver",
    "ResolvedIntent",
    "ContextBuilder",
    "LLMContextPacket",
]
