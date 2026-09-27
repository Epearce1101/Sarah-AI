"""Chat endpoint."""
from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from typing import Optional

import requests
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from backend.api.schemas import ChatRequest, ChatResponse
from backend import llm_models, state
from backend.config import settings
from backend.diagnostics.telemetry import record_chat_error, record_chat_result
from backend.identity import get_user_name
from backend.models.projects import get_conversation_projects, get_project_context
from backend.models.core import add_log
from backend.state import get_sarah

router = APIRouter()
logger = logging.getLogger("sarah.chat")


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


def _warmup_response(original_message: str, started_at: float) -> ChatResponse:
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


def _project_context_for(conversation_id: Optional[int]) -> Optional[str]:
    """Files of the conversation's tagged project, for the prompt only.

    They used to be spliced into payload.message, which then got saved as the
    user's turn — every message in a project chat stored (and re-sent as
    history) the whole project, and the chat view showed it on reload.
    """
    if conversation_id is None:
        return None
    try:
        projects = get_conversation_projects(conversation_id)
        if projects:
            project_id = projects[0]["id"]
            files_context = get_project_context(project_id, max_files=50)
            if files_context:
                logger.info("Attached project context from project %s", project_id)
                return (
                    f"The user is working on project: {projects[0]['name']}\n"
                    f"{projects[0].get('description') or ''}\n\n"
                    f"Project files and code:\n{files_context}"
                )
    except Exception as e:
        logger.warning("Failed to load project context: %s", e)
    return None


def _record_failure(error: Exception, original_message: str, started_at: float) -> None:
    logger.exception("SarahCore chat handling failed")
    record_chat_error(
        message=original_message,
        latency_ms=(time.perf_counter() - started_at) * 1000,
        category=type(error).__name__,
    )
    try:
        add_log(
            "ERROR",
            "chat_failed",
            source="chat",
            payload_json=json.dumps({"error": str(error)}) if str(error) else None,
        )
    except Exception:
        pass


def _chat_response(result, payload: ChatRequest, original_message: str, started_at: float) -> ChatResponse:
    reply = getattr(result, "reply", f"...I'm here, {get_user_name()}.")
    tokens_used = getattr(result, "tokens_used", None)
    token_budget = getattr(result, "token_budget", settings.openrouter_context_window_tokens)
    logger.debug("Token info: tokens_used=%s token_budget=%s", tokens_used, token_budget)

    try:
        add_log(
            "INFO",
            "chat_message",
            source="chat",
            payload_json=json.dumps(
                {
                    "conversation_id": payload.conversation_id,
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
        model=state.LOCAL_LLM_MODEL if state.LLM_MODE == "local" else llm_models.current_online_model(),
        success=True,
    )

    return ChatResponse(
        ok=True,
        reply=reply,
        emotion=getattr(result, "emotion", "neutral"),
        emotion_intensity=float(getattr(result, "emotion_intensity", 0.0) or 0.0),
        affinity_to_creator=float(getattr(result, "affinity_to_creator", 0.0) or 0.0),
        user_message_id=getattr(result, "user_message_id", None),
        assistant_message_id=getattr(result, "assistant_message_id", None),
        tokens_used=tokens_used,
        token_budget=token_budget,
    )


@router.post("/api/chat", response_model=ChatResponse)
async def api_chat(payload: ChatRequest):
    sarah = get_sarah()
    started_at = time.perf_counter()
    original_message = payload.message

    if not await asyncio.to_thread(_ollama_ready):
        _ensure_local_model_loading()
        return _warmup_response(original_message, started_at)

    project_context = _project_context_for(payload.conversation_id)
    try:
        result = await sarah.handle_message(
            message=payload.message,
            from_creator=payload.from_creator,
            conversation_id=payload.conversation_id,
            save_user_message=not payload.regenerate,
            project_context=project_context,
        )
    except Exception as e:
        _record_failure(e, original_message, started_at)
        raise HTTPException(status_code=500, detail=str(e))

    return _chat_response(result, payload, original_message, started_at)


def _sse(event: str, data) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


@router.post("/api/chat/stream")
async def api_chat_stream(payload: ChatRequest):
    """Server-Sent Events version of /api/chat.

    Emits `delta` events ({"text": ...}) as the model writes, then one `done`
    event whose data is exactly the /api/chat JSON (the sanitized, saved
    reply — clients should replace their live preview with it). Failures
    arrive as an `error` event ({"detail": ...}).
    """
    sarah = get_sarah()
    started_at = time.perf_counter()
    original_message = payload.message

    async def events():
        if not await asyncio.to_thread(_ollama_ready):
            _ensure_local_model_loading()
            yield _sse("done", _warmup_response(original_message, started_at).model_dump())
            return

        project_context = _project_context_for(payload.conversation_id)
        try:
            async for event in sarah.handle_message_stream(
                message=payload.message,
                conversation_id=payload.conversation_id,
                save_user_message=not payload.regenerate,
                project_context=project_context,
            ):
                if event["type"] == "delta":
                    yield _sse("delta", {"text": event["text"]})
                else:
                    response = _chat_response(event["reply"], payload, original_message, started_at)
                    yield _sse("done", response.model_dump())
        except Exception as e:
            _record_failure(e, original_message, started_at)
            yield _sse("error", {"detail": str(e)})

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
