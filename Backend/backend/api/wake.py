"""Wake-word polling endpoint."""
from __future__ import annotations

from fastapi import APIRouter

from backend.audio.wake_diagnostics import get_wake_diagnostics, set_wake_event_pending
from backend.audio.wake_loop import is_wake_listener_running, wake_events
import logging

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/api/wake")
def api_wake():
    if not wake_events.empty():
        wake_events.get()
        set_wake_event_pending(not wake_events.empty())
        logger.info("[WAKE] Wake word detected!")
        return {"wake": True}
    set_wake_event_pending(False)
    return {"wake": False}


@router.get("/api/wake/diagnostics")
def api_wake_diagnostics():
    return get_wake_diagnostics(
        event_pending=not wake_events.empty(),
        listener_running=is_wake_listener_running(),
    )
