"""Embodiment endpoints: the renderer reports Sarah's body and senses."""
from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter
from pydantic import BaseModel, Field

from backend.embodiment import get_self
from backend.embodiment.impulse import handle_event

router = APIRouter()


class BodyEvent(BaseModel):
    type: str = Field(..., max_length=32)
    conversation_id: Optional[int] = None
    detail: Dict[str, Any] = Field(default_factory=dict)


@router.post("/api/embodiment/state")
def post_body_state(snapshot: Dict[str, Any]):
    """What her body is doing now (activity, face, gaze, framing, the user's
    presence). Sent by the renderer when it changes."""
    get_self().update_body(snapshot)
    return {"ok": True}


@router.post("/api/embodiment/event")
async def post_body_event(event: BodyEvent):
    """Something happened to her (touch, the user returning, the app
    opening). She remembers it and may say something; a spoken reaction comes
    back as ``reply`` and is already saved to the conversation."""
    return await handle_event(event.type, event.detail, event.conversation_id)


@router.get("/api/embodiment/self")
def get_self_state():
    """Debug view of her present moment."""
    return get_self().snapshot()
