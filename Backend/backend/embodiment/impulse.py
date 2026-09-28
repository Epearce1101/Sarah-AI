"""Body -> mind: things Sarah senses, and whether she says something.

The renderer reports events that happen *to* her (a head pat, the user
coming back to her window, the app opening). Each becomes a sensation she
remembers in the "Right now" block of later prompts. Some also give her the
chance to speak on her own: the model is shown what she just sensed and
answers in her own voice, or chooses silence with ``<silent/>``. Her body has
already reacted on reflex in the renderer; this is the voluntary part.

Guard rails: one spontaneous line at a time, a global cooldown, never while a
chat turn is running, and a line is dropped if the user starts a turn while
it is being written.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime
from typing import Any, Dict, Optional, Tuple

from backend.config import settings
from backend.identity import get_user_name

from .self_model import duration_text, get_self, is_silent

logger = logging.getLogger("sarah.embodiment")

RETURN_SPEAK_AFTER = 10 * 60      # away at least this long -> she may greet
ARRIVE_SPEAK_AFTER = 2 * 60 * 60  # app opened after this long -> she may greet
TOUCH_WINDOW = 120                # touches counted together
TOUCH_COOLDOWN = 12               # being touched is direct: answer sooner

_speaking = asyncio.Lock()


def _voice_enabled() -> bool:
    return bool(settings.presence_voice_enabled)


def _cooldown_seconds() -> float:
    return float(settings.presence_voice_cooldown_seconds)

PERCEPTION_PROMPT = (
    "(This is not a message from {user}. You just sensed something: {sensed}.\n"
    "Your body has already reacted on reflex. Now respond the way you genuinely "
    "would in this moment: say one short, natural line out loud in your own "
    "voice, or stay quiet by writing only <silent/>. Open with <feel>...</feel> "
    "as always. Don't mention this note.)"
)


def _last_message_gap_seconds() -> Optional[float]:
    """Seconds since the last message in any conversation (None if none)."""
    try:
        from backend.models.core import get_connection

        conn = get_connection()
        try:
            row = conn.execute("SELECT MAX(created_at) FROM messages").fetchone()
        finally:
            conn.close()
        if not row or not row[0]:
            return None
        last = datetime.strptime(str(row[0])[:19], "%Y-%m-%d %H:%M:%S")
        return (datetime.utcnow() - last).total_seconds()
    except Exception as exc:
        logger.debug("last message time unavailable: %s", exc)
        return None


def describe(kind: str, detail: Dict[str, Any]) -> Tuple[Optional[str], bool]:
    """(what she sensed, whether it's worth a spoken reaction)."""
    user = get_user_name()
    me = get_self()
    if kind == "touch":
        region = "head" if detail.get("region") == "head" else "body"
        count = len([s for s in me.recent_sensations("touch", TOUCH_WINDOW)]) + 1
        what = f"{user} patted your head" if region == "head" else f"{user} poked you (clicked on your body)"
        if count >= 3:
            what += f", that's {count} times in the last couple of minutes"
        return what, True
    if kind == "returned":
        away = float(detail.get("away_seconds") or 0)
        if away < 120:
            return None, False
        return f"{user} came back to your window after {duration_text(away)} away", away >= RETURN_SPEAK_AFTER
    if kind == "arrived":
        gap = _last_message_gap_seconds()
        if gap is None:
            return f"{user} just opened the app and you've come to; you two haven't talked before", True
        return (
            f"{user} just opened the app and you've come to; you last talked {duration_text(gap)} ago",
            gap >= ARRIVE_SPEAK_AFTER,
        )
    if kind == "dismissed":
        return f"{user} hid your window", False
    if kind == "gesture":
        what = {
            "wave": "waved at you", "thumbs_up": "gave you a thumbs up", "thumbs_down": "gave you a thumbs down",
            "peace": "flashed you a peace sign", "love": "made an 'I love you' sign at you",
            "point_up": "pointed up",
        }.get(str(detail.get("name")), "made a gesture at you")
        # A wave is a greeting: worth a word back. The rest get her body's reaction.
        return f"{user} {what} through your camera", detail.get("name") == "wave"
    if kind == "reminder":
        return f"it's time for a reminder you set: {str(detail.get('text', ''))[:300]}", True
    return None, False


async def handle_event(kind: str, detail: Dict[str, Any], conversation_id: Optional[int]) -> Dict[str, Any]:
    me = get_self()
    sensed, worth_speaking = describe(kind, detail or {})
    if not sensed:
        return {"ok": True, "sensed": None, "spoke": False, "reason": "not significant"}
    me.sense(kind, sensed)

    reason = None
    if not worth_speaking:
        reason = "not worth words"
    elif not _voice_enabled():
        reason = "voice disabled"
    elif conversation_id is None:
        reason = "no conversation"
    elif me.chats_in_flight or time.time() - me.last_chat_started < 5:
        reason = "busy talking"
    elif kind != "reminder" and time.time() - me.last_spoke_at < (min(TOUCH_COOLDOWN, _cooldown_seconds()) if kind == "touch" else _cooldown_seconds()):
        reason = "cooldown"
    elif _speaking.locked():
        reason = "already speaking"
    if reason:
        return {"ok": True, "sensed": sensed, "spoke": False, "reason": reason}

    async with _speaking:
        me.last_spoke_at = time.time()  # claim the cooldown before the slow part
        reply = await _speak(kind, sensed, conversation_id)
    if not reply:
        return {"ok": True, "sensed": sensed, "spoke": False, "reason": "chose silence"}
    return {"ok": True, "sensed": sensed, "spoke": True, "reply": reply}


async def _speak(kind: str, sensed: str, conversation_id: int) -> Optional[Dict[str, Any]]:
    from backend.state import get_sarah

    sarah = get_sarah()
    client = getattr(sarah, "_openrouter", None)
    if client is None or not getattr(sarah, "memory_enabled", False):
        return None
    me = get_self()
    serial = me.chat_serial
    try:
        from backend.usage import using

        with using("presence"):
            response = await client.chat(
                conversation_id=conversation_id,
                user_message=PERCEPTION_PROMPT.format(user=get_user_name(), sensed=sensed),
                save_messages=False,
                save_user_message=False,
            )
    except Exception as exc:
        logger.warning("spontaneous reaction failed: %s", exc)
        return None
    if getattr(response, "finish_reason", "") == "error":
        return None
    content = response.content or ""
    me.observe_reply(conversation_id, content)  # she felt something even if silent
    if is_silent(content):
        logger.info("[PRESENCE] %s -> silent", kind)
        return None
    if me.chat_serial != serial:
        logger.info("[PRESENCE] %s -> dropped (user started talking)", kind)
        return None

    from backend.models.core import add_message

    message_id = add_message(conversation_id, "assistant", content, meta_json=json.dumps({"spontaneous": kind}))
    emotion, intensity = sarah._derive_emotion(conversation_id)
    logger.info("[PRESENCE] %s -> spoke (%d chars)", kind, len(content))
    return {
        "reply": content,
        "emotion": emotion,
        "emotion_intensity": intensity,
        "assistant_message_id": message_id,
        "spontaneous": kind,
    }
