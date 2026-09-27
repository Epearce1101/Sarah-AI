"""Chat endpoint."""
from __future__ import annotations

import asyncio
import json
import logging
import threading
import time

import requests
from fastapi import APIRouter, HTTPException

from backend.api.schemas import ChatRequest, ChatResponse
from backend import state
from backend.config import settings
from backend.diagnostics.telemetry import record_chat_error, record_chat_result
from backend.identity import get_user_name
from backend.models.projects import get_conversation_projects, get_project_context
from backend.models.core import add_log
from backend.state import get_sarah

router = APIRouter()


_warmup_lock = threading.Lock()
_warmup_in_flight: set[str] = set()


def _ollama_ready() -> bool:
    if state.LLM_MODE != "local":
        return True
    try:
        response = requests.get(
            f"{settings.ollama_base_url.rstrip('/')}/api/ps",
            timeout=1.5,
        )
        if response.status_code != 200:
            return False
        data = response.json()
        loaded = data.get("models") if isinstance(data, dict) else []
        target = state.LOCAL_LLM_MODEL or settings.default_local_model
        return any(
            item.get("name") == target or item.get("model") == target
            for item in loaded
            if isinstance(item, dict)
        )
    except Exception:
        return False


def _warm_local_model(model: str) -> None:
    try:
        requests.post(
            f"{settings.ollama_base_url.rstrip('/')}/api/generate",
            json={"model": model, "prompt": "", "keep_alive": "30m"},
            timeout=settings.local_request_timeout_seconds,
        )
    except Exception as exc:
        logging.warning(f"[CHAT] Local model warmup failed for {model}: {exc}")
    finally:
        with _warmup_lock:
            _warmup_in_flight.discard(model)


def _ensure_local_model_loading() -> None:
    """Start loading the local model if it isn't already.

    Ollama unloads a model once its keep_alive lapses (or after a model
    switch). Nothing else reloads it, so without this every chat returned the
    warming-up reply indefinitely.
    """
    model = state.LOCAL_LLM_MODEL or settings.default_local_model
    with _warmup_lock:
        if model in _warmup_in_flight:
            return
        _warmup_in_flight.add(model)
    threading.Thread(
        target=_warm_local_model, args=(model,), name="ollama-warmup", daemon=True
    ).start()


@router.post("/api/chat", response_model=ChatResponse)
async def api_chat(payload: ChatRequest):
    sarah = get_sarah()

    conversation_id = payload.conversation_id
    started_at = time.perf_counter()
    original_message = payload.message

    if not await asyncio.to_thread(_ollama_ready):
        _ensure_local_model_loading()
        warmup_reply = f"I'm still warming up my local model, {get_user_name()}. Try again in a moment."
        record_chat_result(
            message=original_message,
            reply=warmup_reply,
            tokens_used=0,
            token_budget=settings.local_effective_context_tokens,
            latency_ms=(time.perf_counter() - started_at) * 1000,
            success=True,
        )
        return ChatResponse(
            ok=True,
            reply=warmup_reply,
            emotion="thinking",
            emotion_intensity=0.35,
            affinity_to_creator=0.0,
            tokens_used=0,
            token_budget=settings.local_effective_context_tokens,
        )

    # Project files go to the model as separate context. They used to be
    # spliced into payload.message, which then got saved as the user's turn —
    # every message in a project chat stored (and re-sent as history) the
    # whole project, and the chat view showed it on reload.
    project_context = None
    if conversation_id is not None:
        try:
            projects = get_conversation_projects(conversation_id)
            if projects:
                project_id = projects[0]["id"]
                files_context = get_project_context(project_id, max_files=50)
                if files_context:
                    project_context = (
                        f"The user is working on project: {projects[0]['name']}\n"
                        f"{projects[0].get('description') or ''}\n\n"
                        f"Project files and code:\n{files_context}"
                    )
                    logging.info(f"[CHAT] Attached project context from project {project_id}")
        except Exception as e:
            logging.warning(f"[CHAT] Failed to load project context: {e}")

    try:
        logging.info(f"[CHAT] Calling handle_message with conversation_id={conversation_id}")
        result = await sarah.handle_message(
            message=payload.message,
            from_creator=payload.from_creator,
            conversation_id=conversation_id,
            save_user_message=not payload.regenerate,
            project_context=project_context,
        )
    except Exception as e:
        logging.exception("[/api/chat] SarahCore.handle_message failed")
        record_chat_error(
            message=original_message,
            latency_ms=(time.perf_counter() - started_at) * 1000,
            category=type(e).__name__,
        )
        try:
            add_log(
                "ERROR",
                "chat_failed",
                source="chat",
                payload_json=json.dumps({"error": str(e)}) if str(e) else None,
            )
        except Exception:
            pass
        raise HTTPException(status_code=500, detail=str(e))

    reply = getattr(result, "reply", f"...I'm here, {get_user_name()}.")
    emotion = getattr(result, "emotion", "neutral")
    emotion_intensity = getattr(result, "emotion_intensity", 0.0)
    affinity = getattr(result, "affinity_to_creator", 0.0)
    user_message_id = getattr(result, "user_message_id", None)
    assistant_message_id = getattr(result, "assistant_message_id", None)
    tokens_used = getattr(result, "tokens_used", None)
    token_budget = getattr(result, "token_budget", settings.openrouter_context_window_tokens)

    print(f"[CHAT] Token info: tokens_used={tokens_used}, token_budget={token_budget}")

    try:
        add_log(
            "INFO",
            "chat_message",
            source="chat",
            payload_json=json.dumps(
                {
                    "conversation_id": conversation_id,
                    "from_creator": payload.from_creator,
                    "input_len": len(payload.message),
                    "reply_len": len(reply),
                }
            ),
        )
    except Exception:
        pass

    record_chat_result(
        message=original_message,
        reply=reply,
        tokens_used=tokens_used,
        token_budget=token_budget,
        latency_ms=(time.perf_counter() - started_at) * 1000,
        model=state.LOCAL_LLM_MODEL if state.LLM_MODE == "local" else settings.openrouter_model,
        success=True,
    )

    return ChatResponse(
        ok=True,
        reply=reply,
        emotion=emotion,
        emotion_intensity=float(emotion_intensity or 0.0),
        affinity_to_creator=float(affinity or 0.0),
        user_message_id=user_message_id,
        assistant_message_id=assistant_message_id,
        tokens_used=tokens_used,
        token_budget=token_budget,
    )
