"""Reminders Sarah sets (``set_reminder`` tool). A background loop checks
them; when one is due she brings it up in her own words (impulse
"reminder"), pushed to the app through the senses channel."""
from __future__ import annotations

import asyncio
import json
import logging
import threading
from datetime import datetime
from typing import Any, Dict, List, Optional

from .guard import WORKSPACE

logger = logging.getLogger("sarah.reminders")
_FILE = WORKSPACE / "reminders.json"
_lock = threading.Lock()


def _load() -> List[Dict[str, Any]]:
    try:
        return json.loads(_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []


def _save(items: List[Dict[str, Any]]) -> None:
    _FILE.parent.mkdir(parents=True, exist_ok=True)
    _FILE.write_text(json.dumps(items, indent=1), encoding="utf-8")


def add_reminder(text: str, when: datetime, conversation_id: Optional[int] = None) -> None:
    if conversation_id is None:
        from backend.embodiment import get_self
        conversation_id = get_self().last_conversation_id
    with _lock:
        items = _load()
        items.append({"text": text.strip()[:300], "due": when.isoformat(timespec="seconds"),
                      "conversation_id": conversation_id, "done": False})
        _save(items)


def pending() -> List[Dict[str, Any]]:
    with _lock:
        return [r for r in _load() if not r.get("done")]


def _take_due(now: datetime) -> List[Dict[str, Any]]:
    with _lock:
        items = _load()
        due = [r for r in items if not r.get("done") and datetime.fromisoformat(r["due"]) <= now]
        for r in due:
            r["done"] = True
        if due:
            _save(items)
        return due


async def reminder_loop(interval: float = 15.0) -> None:
    from backend.embodiment.impulse import handle_event
    from .senses import senses

    while True:
        try:
            for r in _take_due(datetime.now()):
                out = await handle_event("reminder", {"text": r["text"]}, r.get("conversation_id"))
                if out.get("reason") in ("busy talking", "already speaking"):
                    # Mid-conversation: bring it up in half a minute instead.
                    from datetime import timedelta
                    add_reminder(r["text"], datetime.now() + timedelta(seconds=30), r.get("conversation_id"))
                    continue
                if out.get("spoke"):
                    await senses.push({"type": "say", "reply": out["reply"], "conversation_id": r.get("conversation_id")})
                logger.info("[REMINDER] %s -> %s", r["text"][:60], "spoken" if out.get("spoke") else out.get("reason"))
        except Exception as exc:
            logger.warning("[REMINDER] loop error: %s", exc)
        await asyncio.sleep(interval)
