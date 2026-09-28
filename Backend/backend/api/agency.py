"""Agency endpoints: the senses channel to her body, the Stop button, and
what she has been doing (action log, her tools)."""
from __future__ import annotations

import hmac

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from backend.agency import tools
from backend.agency.reminders import pending as pending_reminders
from backend.agency.senses import senses
from backend.config import settings

router = APIRouter()


@router.websocket("/ws/senses")
async def ws_senses(ws: WebSocket):
    expected = settings.api_token
    if expected and not hmac.compare_digest(ws.headers.get("x-sarah-token", ""), expected):
        await ws.close(code=4401)
        return
    await ws.accept()
    senses.attach(ws)
    try:
        while True:
            msg = await ws.receive_json()
            if msg.get("type") == "response" and msg.get("id"):
                senses.resolve(msg["id"], msg.get("data"))
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        senses.detach(ws)


@router.post("/api/agency/stop")
def agency_stop(seconds: int = 120):
    """Stop button: every tool call is refused for a while."""
    tools.stop(max(5, min(3600, seconds)))
    return {"ok": True, "stopped": True}


@router.post("/api/agency/resume")
def agency_resume():
    tools.resume()
    return {"ok": True, "stopped": False}


@router.get("/api/agency/actions")
def agency_actions(limit: int = 50):
    return {"stopped": tools.stopped(), "actions": tools.recent_actions(max(1, min(500, limit))),
            "reminders": pending_reminders()}


@router.get("/api/agency/initiative")
def initiative_status():
    from backend.agency.mind import mind
    return mind.status()


@router.post("/api/agency/initiative")
def initiative_set(quiet: bool = False):
    """Quiet mode: she keeps watching and remembering but won't speak up or
    act on her own until turned back on."""
    from backend.agency.mind import mind
    mind.quiet = bool(quiet)
    return mind.status()


@router.get("/api/agency/tools")
def agency_tools():
    return {"tools": [{"name": t.name, "description": t.description, "custom": t.custom}
                      for t in tools.all_tools().values()]}
