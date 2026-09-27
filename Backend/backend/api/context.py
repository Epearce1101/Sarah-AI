"""Context-info endpoints."""
from __future__ import annotations

from fastapi import APIRouter

from backend import llm_models, state
from backend.api.settings import _format_model_label
from backend.config import settings
from backend.memory import get_memory_config, get_memory_store
from backend.memory.context_builder import ContextBuilder

router = APIRouter()


def _budget_for_mode(mode: str) -> int:
    return settings.local_effective_context_tokens if mode == "local" else settings.openrouter_context_window_tokens


def _completion_budget_for_mode(mode: str) -> int:
    return settings.local_max_completion_tokens if mode == "local" else settings.llm_max_completion_tokens


def _context_payload(tokens_used: int = 0) -> dict:
    token_budget = _budget_for_mode(state.LLM_MODE)
    provider = "Ollama" if state.LLM_MODE == "local" else "OpenRouter"
    model_name = state.LOCAL_LLM_MODEL if state.LLM_MODE == "local" else llm_models.current_online_model()
    auto_routed = state.LLM_MODE == "online" and model_name == "openrouter/auto"
    model_label = _format_model_label(provider, model_name, auto_routed)
    fit_ratio = tokens_used / token_budget if token_budget else 0
    fit_state = "critical" if fit_ratio >= 0.9 else "warning" if fit_ratio >= 0.7 else "normal"
    return {
        "mode": state.LLM_MODE,
        "local_model": state.LOCAL_LLM_MODEL,
        "provider": provider,
        "model_name": model_name,
        "model_label": model_label,
        "auto_routed": auto_routed,
        "token_budget": token_budget,
        "context_window_tokens": settings.local_context_window_tokens if state.LLM_MODE == "local" else settings.openrouter_context_window_tokens,
        "completion_token_budget": _completion_budget_for_mode(state.LLM_MODE),
        "tokens_used": tokens_used,
        "context_used_tokens": tokens_used,
        "context_fit_state": fit_state,
        "reasoning_effort": settings.openrouter_reasoning_effort if state.LLM_MODE == "online" else "local",
        "online_available": bool(settings.openrouter_api_key),
        "online_fallback_reason": None if settings.openrouter_api_key else "missing_openrouter_api_key",
        **llm_models.status_fields(),
    }


@router.get("/api/context_info")
def api_get_context_info():
    """Return current context info + token budget for the active LLM mode."""
    return _context_payload(0)


def _readonly_packet(conversation_id: int):
    """Rebuild the prompt the model would see next turn, without side effects.

    No fake user turn is appended and mood state is not advanced.
    """
    provider = "Ollama" if state.LLM_MODE == "local" else "OpenRouter"
    model_name = state.LOCAL_LLM_MODEL if state.LLM_MODE == "local" else llm_models.current_online_model()
    builder = ContextBuilder(
        store=get_memory_store(),
        config=get_memory_config(),
    )
    return builder.build(
        conversation_id=conversation_id,
        user_message="",
        llm_mode_info={
            "mode": state.LLM_MODE,
            "model_name": model_name,
            "provider": provider,
            "token_budget": _budget_for_mode(state.LLM_MODE),
            "completion_token_budget": _completion_budget_for_mode(state.LLM_MODE),
        },
        process_mood=False,
        append_user_message=False,
    )


@router.get("/api/context_info/{conversation_id}")
def api_get_context_info_for_conversation(conversation_id: int):
    """Return context info plus the token size of the prompt actually sent.

    Measured from the same ContextBuilder packet the chat path uses (system
    prompt, persona, summaries, trimmed history), so the bar reflects what
    fits the window rather than the raw sum of the last 200 messages.
    """
    tokens_used = 0
    try:
        tokens_used = _readonly_packet(conversation_id).estimated_tokens
    except Exception as e:
        print(f"[ContextInfo] Error estimating tokens: {e}")

    return _context_payload(tokens_used)


@router.get("/api/avatar/context_window/{conversation_id}")
def api_get_avatar_context_window(conversation_id: int):
    """Return the read-only LLM context window for avatar baseline analysis.

    This is intentionally rebuilt from ContextBuilder and not stored anywhere.
    It does not append a fake user turn and does not mutate mood state.
    """
    packet = _readonly_packet(conversation_id)
    return {
        "ok": True,
        "conversation_id": conversation_id,
        "source": "context_builder",
        "read_only": True,
        "messages": packet.messages,
        "estimated_tokens": packet.estimated_tokens,
        "debug_info": {
            "effective_total_token_budget": packet.debug_info.get("effective_total_token_budget"),
            "recent_messages_count": packet.debug_info.get("recent_messages_count"),
            "trimmed_messages_count": packet.debug_info.get("trimmed_messages_count"),
            "readonly_context_window": packet.debug_info.get("readonly_context_window"),
            "fits_total_budget": packet.debug_info.get("fits_total_budget"),
        },
    }
