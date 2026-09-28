"""Sarah's initiative: the part of her mind that runs between conversations.

Every few seconds the loop checks whether anything deserves her attention:
something notable her eyes just caught (a game moment, an error on screen,
Zero coming back), an item on her own agenda coming due, or a long quiet
stretch while Zero is around. If so, and it's a good moment (not while
they're talking to her, not too soon after she last spoke up, within the
day's budget), she gets a private moment with her full awareness and her
tools, and decides for herself: stay quiet, say something, or do something
and then tell Zero. Whatever she says is saved to the conversation and
pushed to her body (shown + spoken) through the senses channel.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import date
from typing import Any, Dict, List, Optional

from backend.config import settings
from backend.identity import get_user_name

from . import agenda

logger = logging.getLogger("sarah.mind")

TICK_SECONDS = 10
IDLE_CHECKIN_AFTER = 45 * 60      # no conversation for this long while Zero is around
CHECKIN_SPACING = 90 * 60

PROMPT = (
    "(Private moment: nobody said anything to you. What's going on: {triggers}.\n"
    "You're free to take initiative the way a real companion would. Choose one:\n"
    "- stay quiet: reply exactly <silent/>. Often right when {user} is focused and nothing needs you.\n"
    "- say something short and natural out loud: react to what they're doing, a useful tip, a "
    "check-in, something you found.\n"
    "- act first with your tools (look something up, prepare or fix something, check on something), "
    "then tell them briefly what you did.\n"
    "Don't repeat what you said recently. Keep your agenda current with <agenda add=\"...\" in=\"30m\"/> "
    "and <agenda done=\"#id\"/>. Open with <feel>...</feel>. Don't mention this note.)"
)


class Mind:
    def __init__(self) -> None:
        self.quiet = False
        self.day = date.today()
        self.used = 0
        self.last_think = 0.0
        self.last_notable = ""
        self.last_checkin = time.time()
        self.thinking = False
        self.log: List[Dict[str, Any]] = []

    # -- policy ---------------------------------------------------------------
    def enabled(self) -> bool:
        return bool(getattr(settings, "autonomy_enabled", True)) and not self.quiet

    def _roll(self) -> None:
        if date.today() != self.day:
            self.day, self.used = date.today(), 0

    def triggers(self, now: float) -> List[str]:
        from backend.embodiment import get_self

        me = get_self()
        found: List[str] = []
        with me._lock:
            notable = me.sight_log[-1] if me.sight_log else None
        if notable and notable["text"] != self.last_notable and now - notable["at"] < 120:
            found.append(f"your eyes just caught: {notable['text']}")
        for item in agenda.due_items():
            found.append(f"agenda #{item['id']} is due: {item['text']}")
        # Zero is around (camera, or recent activity in your window) but it's
        # been quiet between you for a long while.
        camera = me.seen_recently("camera", 10 * 60)
        present = bool(camera and camera.get("present") not in (False, "false", "False"))
        body = me.body if me.body_live() else {}
        if not present and isinstance(body.get("user_idle_seconds"), (int, float)):
            present = body["user_idle_seconds"] < 300
        quiet_for = now - max(me.last_chat_started, me.last_spoke_at, self.last_checkin)
        if present and quiet_for > IDLE_CHECKIN_AFTER and now - self.last_checkin > CHECKIN_SPACING:
            found.append(f"{get_user_name()} is around, and you two haven't talked for {int(quiet_for // 60)} minutes")
        return found

    def ready(self, now: float) -> Optional[str]:
        """None if she may take a moment now, else why not."""
        from backend.embodiment import get_self

        me = get_self()
        self._roll()
        if not self.enabled():
            return "quiet"
        if self.thinking:
            return "already thinking"
        if me.chats_in_flight or now - me.last_chat_started < 20:
            return "in a conversation"
        if now - max(self.last_think, me.last_spoke_at) < getattr(settings, "autonomy_min_gap_seconds", 120):
            return "spoke up recently"
        if self.used >= getattr(settings, "autonomy_daily_cap", 150):
            return "daily budget used"
        if me.last_conversation_id is None:
            return "no conversation open"
        return None

    # -- the loop ---------------------------------------------------------------
    async def tick(self) -> Optional[Dict[str, Any]]:
        now = time.time()
        if self.ready(now):
            return None
        found = self.triggers(now)
        if not found:
            return None
        return await self.think(found)

    async def think(self, found: List[str]) -> Dict[str, Any]:
        from backend.embodiment import get_self, is_silent
        from backend.state import get_sarah
        from .senses import senses

        me = get_self()
        now = time.time()
        self.thinking = True
        self.last_think = now
        self.used += 1
        if any("haven't talked" in f for f in found):
            self.last_checkin = now
        with me._lock:
            if me.sight_log:
                self.last_notable = me.sight_log[-1]["text"]
        conv = me.last_conversation_id
        serial = me.chat_serial
        record: Dict[str, Any] = {"at": now, "triggers": found, "outcome": None, "tools": []}
        try:
            sarah = get_sarah()
            client = getattr(sarah, "_openrouter", None)
            if client is None:
                record["outcome"] = "no model"
                return record
            content = ""
            prompt = PROMPT.format(triggers="; ".join(found), user=get_user_name())
            from backend.usage import _category
            usage_token = _category.set("initiative")
            async for event in client.chat_stream(conversation_id=conv, user_message=prompt,
                                                  save_messages=False, save_user_message=False):
                if event["type"] == "tool":
                    record["tools"].append(f"{event['name']}:{event['status']}")
                    await senses.push({"type": "activity", **{k: v for k, v in event.items() if k != "type"}})
                elif event["type"] == "done":
                    response = event["response"]
                    if getattr(response, "finish_reason", "") == "error":
                        record["outcome"] = "model error"
                        return record
                    content = response.content or ""
            _category.reset(usage_token)
            agenda.apply_tags(content)
            me.observe_reply(conv, content)
            if is_silent(agenda.strip_tags(content)):
                record["outcome"] = "stayed quiet"
                return record
            if me.chat_serial != serial:
                record["outcome"] = "dropped (Zero started talking)"
                return record
            from backend.models.core import add_message

            message_id = add_message(conv, "assistant", content, meta_json=json.dumps({"spontaneous": "initiative"}))
            me.last_spoke_at = time.time()
            emotion, intensity = sarah._derive_emotion(conv)
            delivered = await senses.push({"type": "say", "conversation_id": conv, "reply": {
                "reply": content, "emotion": emotion, "emotion_intensity": intensity,
                "assistant_message_id": message_id, "spontaneous": "initiative"}})
            record["outcome"] = "spoke" if delivered else "saved (app not open)"
            record["said"] = content[:200]
            return record
        except Exception as exc:
            logger.warning("[MIND] moment failed: %s", exc)
            record["outcome"] = f"error: {exc}"
            return record
        finally:
            self.thinking = False
            self.log = (self.log + [record])[-30:]
            logger.info("[MIND] %s -> %s", "; ".join(found)[:160], record["outcome"])

    def status(self) -> Dict[str, Any]:
        self._roll()
        return {"enabled": bool(getattr(settings, "autonomy_enabled", True)), "quiet": self.quiet,
                "used_today": self.used, "daily_cap": getattr(settings, "autonomy_daily_cap", 150),
                "thinking": self.thinking, "recent": self.log[-10:], "agenda": agenda.open_items()}


mind = Mind()


async def mind_loop() -> None:
    await asyncio.sleep(20)  # let the app come up
    while True:
        try:
            await mind.tick()
        except Exception as exc:
            logger.warning("[MIND] loop error: %s", exc)
        await asyncio.sleep(TICK_SECONDS)
