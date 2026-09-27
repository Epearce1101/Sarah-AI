# backend/memory/config.py
"""
Memory System Configuration
===========================
All settings configurable via environment variables with sensible defaults.
"""

import os
from dataclasses import dataclass, field
from typing import Optional

from backend.config import settings as _settings


@dataclass
class MemoryConfig:
    """
    Configuration for Sarah AI's enhanced memory system.

    Token Budget Strategy (5900 token target):
    - Input: system(700) + rolling_summary(900) + task_state(600)
             + chunks(800) + recent_messages(~1500) = ~4500
    - Output: completion(1400)
    - Total: ~5900 tokens
    """

    # ============================================================
    # RECENT MESSAGE WINDOW
    # ============================================================
    # Number of recent messages to keep verbatim in current conversation
    # Older messages are compressed into rolling summaries.
    # Issue #29: this is now a *floor*. The actual fetch limit is computed at
    # runtime from the active context budget × chat_context_ratio so a 1M
    # window pulls thousands of messages, not just 30.
    recent_window_messages: int = 30

    # Fallback if tokens exceed budget - trim to this count
    min_recent_messages: int = 12

    # Issue #29: target fraction of the active model's context budget that
    # should be available for verbatim chat history (vs. system prompt,
    # rolling summary, task state, chunks, completion reserve).
    chat_context_ratio: float = 0.40

    # Issue #29: hard cap on how many messages we will pull from the DB for
    # one context build, regardless of budget. Prevents pathological queries
    # on conversations with tens of thousands of messages.
    recent_window_max_messages: int = 2000

    # Issue #29: conservative average-tokens-per-message used to translate
    # the chat-history token budget into a row count for the DB query.
    avg_tokens_per_message_estimate: int = 80

    # ============================================================
    # CHUNK SUMMARIES
    # ============================================================
    # Create a summary every N messages
    chunk_size: int = 20

    # Max chunks to include in context (oldest get dropped)
    max_chunks_in_context: int = 4

    # Target tokens per chunk summary
    chunk_summary_tokens: int = 200

    # ============================================================
    # ROLLING SUMMARY
    # ============================================================
    # Update rolling summary every N turns (user+assistant pair = 1 turn)
    rolling_update_interval: int = 8

    # Target tokens for rolling summary
    rolling_summary_tokens: int = 900

    # ============================================================
    # TASK STATE (for short reply reliability)
    # ============================================================
    # Max tokens for task state block
    task_state_tokens: int = 600

    # ============================================================
    # TOKEN BUDGETS
    # ============================================================
    # Total target context tokens (5900 target)
    # Allocations: system(700) + rolling(900) + task(600)
    #              + chunks(800) + recent(~1500) + completion(1400) = ~5900
    total_token_budget: int = 5900

    # System prompt allocation
    system_prompt_tokens: int = 700

    # Chars per token estimate (conservative)
    chars_per_token: float = 3.5

    # ============================================================
    # INTENT RESOLVER
    # ============================================================
    # Max words to consider a "short reply"
    short_reply_word_limit: int = 12

    # Confirmation keywords
    confirmation_keywords: tuple = (
        "yes", "yeah", "yep", "sure", "ok", "okay", "go ahead",
        "do it", "proceed", "continue", "go for it", "sounds good",
        "that works", "perfect", "great", "correct", "right",
        "affirmative", "confirmed", "approve", "approved"
    )

    # Negation keywords
    negation_keywords: tuple = (
        "no", "nope", "nah", "don't", "cancel", "stop", "abort",
        "never mind", "forget it", "not now", "wait", "hold on",
        "actually no", "negative", "reject", "rejected"
    )

    # Selection patterns (for numbered choices)
    selection_patterns: tuple = (
        r"^[1-9]$",           # Single digit 1-9
        r"^option\s*[1-9]$",  # "option 1", "option 2"
        r"^choice\s*[1-9]$",  # "choice 1"
        r"^the\s*(first|second|third|fourth|fifth)(\s+one)?$",
        r"^(first|second|third|fourth|fifth)(\s+one)?$",
        r"^[a-d]$",           # Single letter a-d
        r"^letter\s*[a-d]$",  # "letter a"
    )

    # ============================================================
    # LLM SETTINGS (OpenRouter)
    # Defaults sourced from backend.config.settings (single source of truth)
    # ============================================================
    llm_model: str = field(default_factory=lambda: _settings.openrouter_model)
    llm_max_completion_tokens: int = field(default_factory=lambda: _settings.llm_max_completion_tokens)
    llm_temperature: float = field(default_factory=lambda: _settings.llm_temperature)

    # ============================================================
    # DATABASE
    # ============================================================
    # How long to keep chunk summaries (days, 0 = forever)
    chunk_retention_days: int = 0

    # ============================================================
    # DEBUG
    # ============================================================
    debug_memory: bool = False


# Global config singleton
_memory_config: Optional[MemoryConfig] = None


def get_memory_config() -> MemoryConfig:
    """
    Get or create the global MemoryConfig instance.
    Reads from environment variables on first call.
    """
    global _memory_config

    if _memory_config is None:
        _memory_config = MemoryConfig(
            # Recent window - keep last 30 messages verbatim
            # Older messages are compressed into rolling summaries
            recent_window_messages=int(os.environ.get(
                "SARAH_RECENT_WINDOW_MESSAGES", "30"
            )),
            min_recent_messages=int(os.environ.get(
                "SARAH_MIN_RECENT_MESSAGES", "12"
            )),

            # Issue #29: chat-history budget tuning.
            chat_context_ratio=float(os.environ.get(
                "SARAH_CHAT_CONTEXT_RATIO", "0.40"
            )),
            recent_window_max_messages=int(os.environ.get(
                "SARAH_RECENT_WINDOW_MAX_MESSAGES", "2000"
            )),
            avg_tokens_per_message_estimate=int(os.environ.get(
                "SARAH_AVG_TOKENS_PER_MESSAGE", "80"
            )),

            # Chunks
            chunk_size=int(os.environ.get("SARAH_CHUNK_SIZE", "20")),
            max_chunks_in_context=int(os.environ.get(
                "SARAH_MAX_CHUNKS_IN_CONTEXT", "4"
            )),
            chunk_summary_tokens=int(os.environ.get(
                "SARAH_CHUNK_SUMMARY_TOKENS", "200"
            )),

            # Rolling summary
            rolling_update_interval=int(os.environ.get(
                "SARAH_ROLLING_UPDATE_INTERVAL", "8"
            )),
            rolling_summary_tokens=int(os.environ.get(
                "SARAH_ROLLING_SUMMARY_TOKENS", "900"
            )),

            # Task state
            task_state_tokens=int(os.environ.get(
                "SARAH_TASK_STATE_TOKENS", "600"
            )),

            # Token budgets
            total_token_budget=int(os.environ.get(
                "SARAH_TOTAL_TOKEN_BUDGET", "5900"
            )),
            system_prompt_tokens=int(os.environ.get(
                "SARAH_SYSTEM_PROMPT_TOKENS", "700"
            )),
            chars_per_token=float(os.environ.get(
                "SARAH_CHARS_PER_TOKEN", "3.5"
            )),

            # Intent resolver
            short_reply_word_limit=int(os.environ.get(
                "SARAH_SHORT_REPLY_WORD_LIMIT", "12"
            )),

            # LLM (sourced from backend.config.settings — single source of truth)
            llm_model=_settings.openrouter_model,
            llm_max_completion_tokens=_settings.llm_max_completion_tokens,
            llm_temperature=_settings.llm_temperature,

            # Database
            chunk_retention_days=int(os.environ.get(
                "SARAH_CHUNK_RETENTION_DAYS", "0"
            )),

            # Debug
            debug_memory=os.environ.get(
                "SARAH_DEBUG_MEMORY", "false"
            ).lower() in ("true", "1", "yes"),
        )

        if _memory_config.debug_memory:
            print(f"[MemoryConfig] Loaded: {_memory_config}")

    return _memory_config


def reset_memory_config():
    """Reset config singleton (useful for testing)."""
    global _memory_config
    _memory_config = None
