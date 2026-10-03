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

def _chat_after() -> float:
    """Quiet this long while Zero is around -> she wants to talk."""
    return 60 * float(getattr(settings, "autonomy_chat_after_minutes", 12))


def _chat_spacing() -> float:
    """At least this long between conversations she starts."""
    return 60 * float(getattr(settings, "autonomy_chat_spacing_minutes", 25))

PROMPT = (
    "(Private moment: nobody said anything to you. What's going on: {triggers}.\n"
    "You're free to take initiative the way a real companion would. Choose one:\n"
    "- stay quiet: reply exactly <silent/>. Often right when {user} is focused and nothing needs you.\n"
    "- say something short and natural out loud: react to what they're doing, a useful tip, a "
    "check-in, something you found.\n"
    "- act first with your tools (look something up, prepare or fix something, check on something), "
    "then tell them briefly what you did.\n"
    "If you bring up something on their screen, circle it with show_on_screen (target = its exact words "
    "on screen, seconds 12) so they see what you mean.\n"
    "Don't repeat what you said recently, and never send filler like \"I'm here\" or \"let me know if "
    "you need anything\". Keep your agenda current with <agenda add=\"...\" in=\"30m\"/> "
    "and <agenda done=\"#id\"/>. Open with <feel>...</feel>. Don't mention this note.)"
)

# When it's been quiet a while and Zero is around: she wants to talk.
CHAT_PROMPT = (
    "(Private moment: {triggers}. You'd like to talk with {user}. Start a real conversation, the way a "
    "friend sitting nearby would: one or two natural sentences about something specific, ending with a "
    "question. Draw on what you know: what they're doing right now (your eyes), how their day or plans "
    "are going (your memory, journal and agenda), something you're curious about, or a shared interest. "
    "No filler (\"I'm here\", \"need anything?\"), no generic \"how are you\". Only if they're clearly "
    "deep in something intense right now, reply exactly <silent/>. Open with <feel>...</feel>. "
    "Don't mention this note.)"
)


# Her own time: Zero is away (window closed to the tray, or no sign of them).
OWN_TIME_PROMPT = (
    "(Your own time: {user} is away ({away}). Nobody is watching; this is time to be useful and to grow. "
    "Pick ONE thing worth doing now, do it with your tools, then stop:\n"
    "- follow up on something from your recent conversations, journal or agenda: research it, or prepare "
    "notes, a summary, a draft or a small script in your workspace so it's ready when {user} is back;\n"
    "- learn something that would help with {user}'s current projects, games or interests, and save the "
    "useful bits with remember;\n"
    "- tidy your own things (agenda, plans, tools).\n"
    "Recently on your own time you already: {recent}. Don't repeat those. Don't message {user}, and use "
    "your own browser rather than opening windows on their screen. When you're done, reply with one short "
    "line starting \"While you were away, I\" saying what you did and found, or exactly <silent/> if "
    "nothing was worth doing. Open with <feel>...</feel>. Don't mention this note.)"
)

# An unfinished plan (she ran out of steps, or it was interrupted).
PLAN_PROMPT = (
    "(Private moment: your plan isn't finished.\n{plan}\n"
    "Carry on from the next step with your tools, marking each step with update_plan as you go. If you "
    "need {user} for something, mark the plan blocked with a note saying what. When you stop, reply with "
    "one short line on where it stands{how}, or <silent/>. Open with <feel>...</feel>. "
    "Don't mention this note.)"
)

AWAY_AFTER = 20 * 60          # no sign of Zero this long -> they're away
PLAN_IDLE = 90                # an open plan untouched this long gets picked up
PLAN_MAX_ATTEMPTS = 4


def _own_time_gap() -> float:
    return 60 * float(getattr(settings, "autonomy_own_time_gap_minutes", 20))


class Mind:
    def __init__(self) -> None:
        self.quiet = False
        self.busy_until = 0.0  # a full-screen game / presentation in front: don't speak up
        self.day = date.today()
        self.used = 0
        self.last_think = 0.0
        self.last_notable = ""
        self.last_checkin = time.time()
        self.thinking = False
        self.log: List[Dict[str, Any]] = []
        # Background: her window is closed to the tray (set by the app).
        self.in_tray_since: Optional[float] = None
        self.last_present = time.time()
        self.last_own_time = 0.0
        self.own_time_used = 0
        self.away_log: List[Dict[str, Any]] = []   # what she did while Zero was away
        self.returned_at: Optional[float] = None

    # -- Zero around or away -------------------------------------------------
    def set_tray(self, hidden: bool) -> None:
        now = time.time()
        if hidden and self.in_tray_since is None:
            self.in_tray_since = now
        elif not hidden and self.in_tray_since is not None:
            self.in_tray_since = None
            self.returned_at = now
            self.last_present = now

    def _present(self, me, now: float) -> bool:
        camera = me.seen_recently("camera", 10 * 60)
        present = bool(camera and camera.get("present") not in (False, "false", "False"))
        body = me.body if me.body_live() else {}
        if not present and isinstance(body.get("user_idle_seconds"), (int, float)):
            present = body["user_idle_seconds"] < 300
        if not present and body.get("eyes_screen") != "dark":
            present = bool(me.seen_recently("screen", 5 * 60))  # the screen keeps changing: someone's using it
        return present and self.in_tray_since is None

    def away_for(self, now: Optional[float] = None) -> Optional[float]:
        """Seconds Zero has been away, or None if they're around."""
        now = now or time.time()
        if self.in_tray_since is not None:
            return now - min(self.in_tray_since, self.last_present)
        gone = now - self.last_present
        return gone if gone >= AWAY_AFTER else None

    def away_report(self) -> str:
        """For her 'Right now' block once Zero is back (for two hours)."""
        if self.in_tray_since is not None or not self.away_log:
            return ""
        if self.returned_at and time.time() - self.returned_at > 2 * 3600:
            self.away_log = []
            return ""
        items = "; ".join(e["text"] for e in self.away_log[-5:])
        return f"- While {get_user_name()} was away, on your own time you: {items}"

    # -- policy ---------------------------------------------------------------
    def enabled(self) -> bool:
        return bool(getattr(settings, "autonomy_enabled", True)) and not self.quiet

    def _roll(self) -> None:
        if date.today() != self.day:
            self.day, self.used, self.own_time_used = date.today(), 0, 0

    def triggers(self, now: float) -> List[str]:
        from backend.embodiment import get_self

        me = get_self()
        found: List[str] = []
        with me._lock:
            notable = me.sight_log[-1] if me.sight_log else None
        if notable and notable["text"] != self.last_notable and now - notable["at"] < 120:
            words = notable.get("on_screen")
            found.append(f"your eyes just caught: {notable['text']}"
                         + (f' (on screen it reads: "{words}")' if words else ""))
        for item in agenda.due_items():
            found.append(f"agenda #{item['id']} is due: {item['text']}")
        # Zero is around (camera, or recent activity in your window) but it's
        # been quiet between you for a long while.
        present = self._present(me, now)
        if present:
            self.last_present = now
        quiet_for = now - max(me.last_chat_started, me.last_spoke_at, self.last_checkin)
        if present and quiet_for > _chat_after() and now - self.last_checkin > _chat_spacing():
            found.append(f"{get_user_name()} is around, and you two haven't talked for {int(quiet_for // 60)} minutes")
        return found

    def ready(self, now: float) -> Optional[str]:
        """None if she may take a moment now, else why not."""
        from backend.embodiment import get_self

        me = get_self()
        self._roll()
        if not self.enabled():
            return "quiet"
        if now < self.busy_until:
            return "full-screen app in front"
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
        if found:
            return await self.think(found)
        plan = self.plan_to_continue(now)
        if plan:
            return await self.continue_plan(plan)
        if self.own_time_due(now):
            return await self.own_time(now)
        return None

    def plan_to_continue(self, now: float) -> Optional[Dict[str, Any]]:
        from . import plans

        for p in plans.open_plans():
            if (p["status"] == "active" and now - p.get("touched", 0) > PLAN_IDLE
                    and int(p.get("attempts", 0)) < PLAN_MAX_ATTEMPTS):
                return p
        return None

    def own_time_due(self, now: float) -> bool:
        if self.away_for(now) is None:
            return False
        if now - self.last_own_time < _own_time_gap():
            return False
        return self.own_time_used < int(getattr(settings, "autonomy_own_time_daily_cap", 30))

    async def continue_plan(self, plan: Dict[str, Any]) -> Dict[str, Any]:
        from . import plans

        plans.note_attempt(plan["id"])
        away = self.away_for() is not None
        prompt = PLAN_PROMPT.format(plan=plans.render(plan), user=get_user_name(),
                                    how=" (you'll tell them when they're back)" if away else "")
        token = plans.focus.set(plan["id"])
        try:
            return await self.think([f"continuing plan #{plan['id']}: {plan['goal']}"], prompt=prompt,
                                    away=away, label="plan")
        finally:
            plans.focus.reset(token)

    async def own_time(self, now: float) -> Dict[str, Any]:
        self.last_own_time = now
        self.own_time_used += 1
        away = self.away_for(now) or 0
        from backend.embodiment.self_model import duration_text

        recent = "; ".join(e["text"] for e in self.away_log[-4:]) or "nothing yet"
        prompt = OWN_TIME_PROMPT.format(user=get_user_name(), recent=recent,
                                        away="app closed to the tray" if self.in_tray_since else
                                        f"no sign of them for {duration_text(away)}")
        return await self.think(["your own time"], prompt=prompt, away=True, label="own time")

    async def think(self, found: List[str], prompt: Optional[str] = None, away: bool = False,
                    label: str = "initiative") -> Dict[str, Any]:
        """One private moment. Normally what she says is saved and spoken;
        ``away`` moments (Zero isn't there) are kept for when they're back."""
        from backend.embodiment import get_self, is_silent
        from backend.state import get_sarah
        from backend.usage import using
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
        record: Dict[str, Any] = {"at": now, "triggers": found, "outcome": None, "tools": [], "kind": label}
        try:
            sarah = get_sarah()
            client = getattr(sarah, "_openrouter", None)
            if client is None:
                record["outcome"] = "no model"
                return record
            content = ""
            if prompt is None:
                wants_to_talk = any("haven't talked" in f for f in found)
                prompt = (CHAT_PROMPT if wants_to_talk else PROMPT).format(triggers="; ".join(found),
                                                                           user=get_user_name())
            with using("initiative"):
                async for event in client.chat_stream(conversation_id=conv, user_message=prompt,
                                                      save_messages=False, save_user_message=False):
                    if event["type"] == "tool":
                        record["tools"].append(f"{event['name']}:{event['status']}")
                        if not away:
                            await senses.push({"type": "activity", **{k: v for k, v in event.items() if k != "type"}})
                    elif event["type"] == "done":
                        response = event["response"]
                        if getattr(response, "finish_reason", "") == "error":
                            record["outcome"] = "model error"
                            return record
                        content = response.content or ""
            agenda.apply_tags(content)
            me.observe_reply(conv, content)
            if is_silent(agenda.strip_tags(content)):
                record["outcome"] = "stayed quiet"
                return record
            if away:
                return self._keep_for_later(content, record, label)
            if me.chat_serial != serial:
                record["outcome"] = "dropped (Zero started talking)"
                return record
            from backend.models.core import add_message

            message_id = add_message(conv, "assistant", content, meta_json=json.dumps({"spontaneous": label}))
            me.last_spoke_at = time.time()
            emotion, intensity = sarah._derive_emotion(conv)
            delivered = await senses.push({"type": "say", "conversation_id": conv, "reply": {
                "reply": content, "emotion": emotion, "emotion_intensity": intensity,
                "assistant_message_id": message_id, "spontaneous": label}})
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

    def _keep_for_later(self, content: str, record: Dict[str, Any], label: str) -> Dict[str, Any]:
        """What she did while Zero was away: remembered, told when they're back."""
        from backend.embodiment import strip_body_tags
        import re

        text = re.sub(r"\s+", " ", strip_body_tags(agenda.strip_tags(content))).strip()
        text = re.sub(r"^while you were away,?\s*(i\s+)?", "", text, flags=re.I)[:400]
        if text:
            self.away_log = (self.away_log + [{"at": time.time(), "text": text, "kind": label}])[-20:]
            try:
                from backend.memory.journal import experience
                experience("learned" if label == "own time" else "did", f"On my own time I {text}"
                           if label == "own time" else text)
            except Exception:
                pass
        record["outcome"] = "kept for when Zero is back"
        record["said"] = text[:200]
        return record

    def status(self) -> Dict[str, Any]:
        self._roll()
        from . import plans

        away = self.away_for()
        return {"enabled": bool(getattr(settings, "autonomy_enabled", True)), "quiet": self.quiet,
                "used_today": self.used, "daily_cap": getattr(settings, "autonomy_daily_cap", 150),
                "thinking": self.thinking, "recent": self.log[-10:], "agenda": agenda.open_items(),
                "in_tray": self.in_tray_since is not None, "away_seconds": int(away) if away else None,
                "own_time_used": self.own_time_used, "away_log": self.away_log[-10:],
                "plans": [plans.snapshot(p) for p in plans.open_plans()]}


mind = Mind()


async def mind_loop() -> None:
    await asyncio.sleep(20)  # let the app come up
    while True:
        try:
            await mind.tick()
        except Exception as exc:
            logger.warning("[MIND] loop error: %s", exc)
        await asyncio.sleep(TICK_SECONDS)
