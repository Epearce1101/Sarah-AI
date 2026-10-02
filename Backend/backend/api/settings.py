"""Settings + LLM-mode endpoints."""
from __future__ import annotations

import logging
import os
from typing import Dict

from fastapi import APIRouter, HTTPException

from backend import llm_models, state, user_notes
from backend.api.schemas import SettingUpdate
from backend.config import settings
from backend.models.core import get_all_settings, get_setting, set_setting
from backend.state import get_sarah, set_llm_mode

logger = logging.getLogger(__name__)

router = APIRouter()


def _format_model_label(provider: str, model_name: str, auto_routed: bool) -> str:
    """'nvidia/nemotron-3-ultra-550b-a55b:free' -> 'OpenRouter - Nemotron 3 Ultra 550B A55B (free)'."""
    if provider == "Ollama":
        return "Local fallback"
    if auto_routed:
        return "OpenRouter Auto"
    name = str(model_name or llm_models.current_online_model()).split("/", 1)[-1]
    base, _, variant = name.partition(":")
    short = base.replace("-", " ").title()
    return f"OpenRouter - {short}" + (f" ({variant})" if variant else "")


def _llm_status_payload() -> Dict[str, object]:
    provider = "Ollama" if state.LLM_MODE == "local" else "OpenRouter"
    model_name = state.LOCAL_LLM_MODEL if state.LLM_MODE == "local" else llm_models.current_online_model()
    token_budget = settings.local_effective_context_tokens if state.LLM_MODE == "local" else settings.openrouter_context_window_tokens
    context_window_tokens = settings.local_context_window_tokens if state.LLM_MODE == "local" else settings.openrouter_context_window_tokens
    completion_token_budget = settings.local_max_completion_tokens if state.LLM_MODE == "local" else settings.llm_max_completion_tokens
    auto_routed = state.LLM_MODE == "online" and model_name == "openrouter/auto"
    model_label = _format_model_label(provider, model_name, auto_routed)
    return {
        "mode": state.LLM_MODE,
        "local_model": state.LOCAL_LLM_MODEL,
        "provider": provider,
        "model_name": model_name,
        "model_label": model_label,
        "auto_routed": auto_routed,
        "token_budget": token_budget,
        "context_window_tokens": context_window_tokens,
        "completion_token_budget": completion_token_budget,
        "reasoning_effort": settings.openrouter_reasoning_effort if state.LLM_MODE == "online" else "local",
        "online_available": bool(settings.openrouter_api_key),
        "online_fallback_reason": None if settings.openrouter_api_key else "missing_openrouter_api_key",
        **llm_models.status_fields(),
    }


@router.get("/api/models")
def api_list_models(refresh: bool = False):
    """OpenRouter catalog for the UI model picker (free models first)."""
    if refresh:
        llm_models.fetch_catalog(max_age=0)
    models = llm_models.catalog_summary()
    return {
        "ok": bool(models),
        "current": llm_models.current_online_model(),
        "fallbacks": llm_models.fallback_models(),
        "models": models,
    }


@router.post("/api/llm_model")
def api_set_llm_model(payload: Dict[str, str]):
    """Switch the online chat model at runtime; persisted across restarts."""
    try:
        llm_models.set_online_model(payload.get("model") or "")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"ok": True, **_llm_status_payload()}


@router.get("/api/settings")
def api_get_settings():
    return get_all_settings()


@router.get("/api/settings/{key}")
def api_get_setting(key: str):
    return {"key": key, "value": get_setting(key)}


@router.post("/api/settings")
def api_set_setting(payload: SettingUpdate):
    set_setting(payload.key, payload.value)
    return {"ok": True, "key": payload.key, "value": payload.value}


@router.get("/api/user_notes")
def api_get_user_notes():
    """Zero's standing notes for Sarah (Functions tab)."""
    return {"ok": True, **user_notes.get_notes(), "max_chars": user_notes.MAX_CHARS}


@router.post("/api/user_notes")
def api_save_user_notes(payload: Dict[str, str]):
    """Save the notes; they apply to every conversation from her next reply."""
    try:
        saved = user_notes.save_notes(payload.get("notes") or "")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"ok": True, **saved, "max_chars": user_notes.MAX_CHARS}


@router.get("/api/llm_mode")
def api_get_llm_mode():
    return _llm_status_payload()


@router.post("/api/llm_mode")
def api_set_llm_mode(payload: Dict[str, str]):
    mode = (payload.get("mode") or "").lower()
    local_model = payload.get("local_model") or state.LOCAL_LLM_MODEL

    if mode not in ("online", "local"):
        raise HTTPException(status_code=400, detail="Invalid LLM mode.")
    if mode == "online" and not settings.openrouter_api_key:
        logging.warning("[LLM MODE] OpenRouter key missing; keeping Sarah in local mode.")
        mode = "local"

    set_llm_mode(mode, local_model)

    os.environ["SARAH_LLM_MODE"] = state.LLM_MODE
    os.environ["SARAH_LOCAL_MODEL"] = state.LOCAL_LLM_MODEL

    logger.info(f"[LLM MODE] Changing mode to {state.LLM_MODE} (local_model={state.LOCAL_LLM_MODEL})")

    try:
        sarah = get_sarah()
        if sarah:
            sarah.set_llm_mode(state.LLM_MODE, state.LOCAL_LLM_MODEL)
            logger.info(f"[LLM MODE] SarahCore now using mode={state.LLM_MODE}, "
                f"local_model={state.LOCAL_LLM_MODEL}")
        else:
            logger.info("[LLM MODE] SarahCore not initialized yet.")
    except Exception as e:
        logging.warning(f"[LLM MODE] Failed to update SarahCore: {e}")

    return {"ok": True, **_llm_status_payload()}
