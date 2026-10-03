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


@router.websocket("/ws/chrome")
async def ws_chrome(ws: WebSocket):
    """The Sarah Browser Bridge extension in Zero's Chrome. Browsers set the
    Origin header themselves, so web pages can't pose as the extension."""
    from backend.agency.chrome_bridge import ALLOWED_ORIGIN, bridge

    if ws.headers.get("origin", "") != ALLOWED_ORIGIN:
        await ws.close(code=4403)
        return
    await ws.accept()
    bridge.attach(ws)
    try:
        while True:
            bridge.on_message(await ws.receive_json())
    except (WebSocketDisconnect, RuntimeError, ValueError):
        pass
    finally:
        bridge.detach(ws)


@router.get("/api/agency/chrome")
def chrome_status():
    from backend.agency.chrome_bridge import bridge
    from pathlib import Path
    return {**bridge.status(), "folder": str(Path(__file__).resolve().parents[3] / "chrome-extension")}


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


@router.post("/api/agency/background")
def background_set(hidden: bool = False):
    """The app window went to the tray (hidden) or came back: while it's in
    the tray, Zero is away and she has her own time."""
    from backend.agency.mind import mind
    mind.set_tray(bool(hidden))
    return mind.status()


@router.get("/api/agency/plans")
def plans_list():
    from backend.agency import plans
    return {"open": [plans.snapshot(p) for p in plans.open_plans()]}


@router.get("/api/memory/episodes")
def episodes(query: str = "", day: str = "", limit: int = 10):
    """Search her episodic memory (by meaning, or a day), plus counts."""
    from backend.memory import episodic
    found = episodic.on_day(day) if day else episodic.search(query, k=max(1, min(50, limit)), min_score=episodic.MIN_SCORE - 0.1) \
        if query else []
    return {"stats": episodic.stats(), "results": found}


@router.get("/api/journal")
def journal_entries(limit: int = 30):
    """Her journal (one entry per day) and today's experiences so far."""
    from datetime import date
    from backend.memory import journal
    return {"entries": journal.entries(max(1, min(365, limit))),
            "today": journal.experiences(date.today().isoformat())}


@router.post("/api/journal/write")
async def journal_write(day: str):
    """Write (or rewrite) the entry for a day now, e.g. 2026-09-27."""
    from backend.memory import journal
    result = await journal.write_day(day)
    return {"ok": bool(result), "entry": result}


@router.get("/api/usage")
def usage_today():
    """Today's free-model requests (UTC day) by what she spent them on."""
    from backend import usage
    return usage.today()


@router.get("/api/agency/tools")
def agency_tools():
    return {"tools": [{"name": t.name, "description": t.description, "custom": t.custom}
                      for t in tools.all_tools().values()]}


@router.get("/api/agency/locate")
def agency_locate(target: str = ""):
    """Where an app, file or folder is on screen (physical pixels), so pet-mode
    Sarah can point at it. ``found: false`` when it isn't visible."""
    from backend.agency.locate import locate
    try:
        return locate(target[:400])
    except Exception as exc:  # UI Automation hiccups must never break a reply
        return {"found": False, "reason": str(exc)[:200]}


@router.get("/api/agency/foreground")
def agency_foreground():
    """The window Zero is using (not Sarah's), for pet mode to stand on."""
    from backend.agency.locate import foreground
    try:
        return foreground()
    except Exception as exc:
        return {"found": False, "reason": str(exc)[:200]}
