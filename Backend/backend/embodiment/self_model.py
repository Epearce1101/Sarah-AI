"""Sarah's present moment: one self shared by her mind and her body.

Before this, the model wrote text and a separate avatar tried to act it out;
her "emotion" was even guessed from the *user's* message. Now:

- Her replies open with how she feels (``<feel>happy:0.6 | why</feel>``).
  That feeling is hers: it sets her mood, voice, face and posture.
- The renderer reports what her body is doing and what she senses (being
  touched, the user typing, coming back, the window hidden).
- Every prompt describes this moment back to her (``render_now``), so she
  knows her own body the way a person does, and body events can reach her
  mind and make her speak (``backend.embodiment.impulse``).

State is process-local and in memory; the feeling is also written through to
the per-conversation ``MoodState`` so it survives restarts.
"""
from __future__ import annotations

import logging
import re
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Deque, Dict, List, Optional

logger = logging.getLogger("sarah.embodiment")

# ---------------------------------------------------------------------------
# Feelings
# ---------------------------------------------------------------------------

_FEEL_RE = re.compile(r"<feel\b[^>]*>([^<]*)</feel\s*>", re.IGNORECASE)
_FACE_RE = re.compile(r"<face\b[^>]*>([^<]*)</face\s*>", re.IGNORECASE)
# Every body tag, for text that should read as plain prose (summaries, memory).
_BODY_TAG_RE = re.compile(
    r"<(feel|face|look|point|gesture|motion)\b[^>]*>[^<]*</\1\s*>|<silent\s*/?>",
    re.IGNORECASE,
)

# Her words for feelings -> the 7 mood-engine emotions (style + persistence).
_FEEL_TO_MOOD = {
    "happy": "happy", "smile": "happy", "glad": "happy", "content": "happy", "warm": "happy",
    "affectionate": "happy", "loving": "happy", "shy": "happy", "playful": "happy",
    "amused": "happy", "proud": "happy", "smug": "happy", "relieved": "happy",
    "grateful": "happy", "relaxed": "neutral", "calm": "neutral", "neutral": "neutral",
    "sleepy": "neutral", "tired": "neutral", "bored": "neutral",
    "excited": "excited", "surprised": "excited", "delighted": "excited", "thrilled": "excited",
    "curious": "confused", "thinking": "confused", "confused": "confused", "unsure": "confused",
    "sad": "sad", "hurt": "sad", "lonely": "sad", "worried": "sad", "cry": "sad", "guilty": "sad",
    "angry": "angry", "mad": "angry",
    "annoyed": "frustrated", "frustrated": "frustrated", "irritated": "frustrated", "pout": "frustrated",
}


@dataclass
class Feeling:
    label: str
    intensity: float = 0.5
    reason: str = ""
    at: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {"label": self.label, "intensity": round(self.intensity, 2), "reason": self.reason, "at": self.at}


def parse_feel(text: str) -> Optional[Feeling]:
    """First ``<feel>`` in a reply: ``label[:0.7| 0.7] [| reason]``."""
    m = _FEEL_RE.search(text or "")
    if not m:
        return None
    body, _, reason = m.group(1).partition("|")
    parts = re.split(r"[:\s]+", body.strip().lower(), maxsplit=1)
    label = re.sub(r"[^a-z_ -]", "", parts[0]).strip().replace(" ", "_")
    if not label:
        return None
    intensity = 0.5
    if len(parts) > 1:
        try:
            intensity = float(parts[1].strip())
        except ValueError:
            pass
    return Feeling(label=label, intensity=max(0.0, min(1.0, intensity)), reason=reason.strip()[:160])


def feeling_from_reply(text: str) -> Optional[Feeling]:
    """Her feeling as written in a reply; falls back to her first <face>."""
    feel = parse_feel(text)
    if feel:
        return feel
    m = _FACE_RE.search(text or "")
    if m:
        label, _, amount = m.group(1).strip().lower().partition(":")
        try:
            intensity = float(amount) if amount else 0.5
        except ValueError:
            intensity = 0.5
        label = label.strip().replace(" ", "_")
        if label:
            return Feeling(label=label, intensity=max(0.0, min(1.0, intensity)))
    return None


def mood_emotion_for(label: str) -> str:
    return _FEEL_TO_MOOD.get((label or "").lower(), "neutral")


def strip_body_tags(text: str) -> str:
    """Reply text without body tags (for summaries and memory)."""
    return re.sub(r"[ \t]{2,}", " ", _BODY_TAG_RE.sub("", text or "")).strip()


def is_silent(text: str) -> bool:
    """She chose not to say anything (only tags, or <silent/>)."""
    if re.search(r"<silent\s*/?>", text or "", re.IGNORECASE):
        return True
    return not strip_body_tags(text).strip(" .…\n")


# ---------------------------------------------------------------------------
# The shared self
# ---------------------------------------------------------------------------

@dataclass
class Sensation:
    kind: str
    text: str
    at: float = field(default_factory=time.time)


def _ago(seconds: float) -> str:
    seconds = max(0, int(seconds))
    if seconds < 10:
        return "just now"
    if seconds < 60:
        return f"{seconds} s ago"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes} min ago"
    hours = minutes // 60
    return f"{hours} h {minutes % 60} min ago" if minutes % 60 else f"{hours} h ago"


def duration_text(seconds: float) -> str:
    seconds = max(0, int(seconds))
    if seconds < 90:
        return f"{seconds} seconds"
    minutes = round(seconds / 60)
    if minutes < 90:
        return f"{minutes} minutes"
    hours = seconds / 3600
    if hours < 36:
        return f"{hours:.0f} hours" if hours >= 2 else "about an hour"
    return f"{hours / 24:.0f} days"


def _look_words(target: str, user_name: str) -> str:
    key = target.lower().strip()
    words = {
        "user": user_name, "you": user_name, "viewer": user_name, "camera": user_name,
        "chat": "the chat messages", "messages": "the chat messages", "conversation": "the chat messages",
        "input": "the message box", "keyboard": "the message box", "typing": "the message box",
        "cursor": f"{user_name}'s mouse pointer", "mouse": f"{user_name}'s mouse pointer",
        "left": "off to one side", "right": "off to one side", "away": "away, off to the side",
        "up": "upward, lost in thought", "sky": "upward, lost in thought",
        "down": "down", "floor": "down at the floor", "self": "yourself", "me": "yourself",
        "screen": "the top of the window", "top": "the top of the window", "sidebar": "the sidebar", "menu": "the sidebar",
    }
    return words.get(key, target)


class SelfModel:
    BODY_STALE_SECONDS = 120
    FEELING_FRESH_SECONDS = 45 * 60
    SENSATION_WINDOW_SECONDS = 10 * 60

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.feelings: Dict[int, Feeling] = {}
        self.body: Dict[str, Any] = {}
        self.body_at: float = 0.0
        self.sensations: Deque[Sensation] = deque(maxlen=24)
        self.perceived: Dict[int, str] = {}     # conversation -> how the user seemed
        self.modality: Dict[int, tuple] = {}    # conversation -> ("voice"|"text", at)
        self.sight: Dict[str, Dict[str, Any]] = {}   # "screen"/"camera" -> observation + "at"
        self.sight_log: Deque[Dict[str, Any]] = deque(maxlen=30)  # notable things seen
        self.chats_in_flight = 0
        self.chat_serial = 0          # bumps on every chat turn (race detection)
        self.last_chat_started = 0.0
        self.last_spoke_at = 0.0

    # -- body & senses (from the renderer) ---------------------------------
    def update_body(self, snapshot: Dict[str, Any]) -> None:
        clean = {k: v for k, v in (snapshot or {}).items() if isinstance(k, str) and k and len(k) < 40}
        for k, v in list(clean.items()):
            if isinstance(v, str):
                clean[k] = v[:120]
            elif not isinstance(v, (int, float, bool)) and v is not None:
                clean.pop(k)
        with self._lock:
            self.body = clean
            self.body_at = time.time()

    def body_live(self) -> bool:
        return bool(self.body) and time.time() - self.body_at < self.BODY_STALE_SECONDS

    def sense(self, kind: str, text: str) -> Sensation:
        s = Sensation(kind=kind, text=text)
        with self._lock:
            self.sensations.append(s)
        return s

    def recent_sensations(self, kind: Optional[str] = None, window: Optional[float] = None) -> List[Sensation]:
        cutoff = time.time() - (window or self.SENSATION_WINDOW_SECONDS)
        with self._lock:
            return [s for s in self.sensations if s.at >= cutoff and (kind is None or s.kind == kind)]

    def perceive_user(self, conversation_id: int, description: Optional[str]) -> None:
        with self._lock:
            if description:
                self.perceived[conversation_id] = description
            else:
                self.perceived.pop(conversation_id, None)

    def see(self, observation: Dict[str, Any]) -> None:
        """Store what her eyes just reported (see backend.perception.sight)."""
        now = time.time()
        with self._lock:
            for kind in ("screen", "camera"):
                data = observation.get(kind)
                if isinstance(data, dict) and data:
                    self.sight[kind] = {**{k: v for k, v in data.items() if isinstance(v, (str, bool, int, float))}, "at": now}
            notable = observation.get("notable")
            if isinstance(notable, str) and notable.strip() and notable.strip().lower() not in ("null", "none"):
                self.sight_log.append({"text": notable.strip()[:200], "at": now})

    def seen_recently(self, kind: str, max_age: float) -> Optional[Dict[str, Any]]:
        with self._lock:
            s = self.sight.get(kind)
        return s if s and time.time() - s["at"] < max_age else None

    def note_modality(self, conversation_id: Optional[int], modality: str) -> None:
        """How the user's latest turn reached her: said out loud or typed."""
        if conversation_id is None:
            return
        with self._lock:
            self.modality[conversation_id] = ((modality or "text").lower(), time.time())

    # -- chat bookkeeping ----------------------------------------------------
    def chat_started(self) -> None:
        with self._lock:
            self.chats_in_flight += 1
            self.chat_serial += 1
            self.last_chat_started = time.time()

    def chat_finished(self) -> None:
        with self._lock:
            self.chats_in_flight = max(0, self.chats_in_flight - 1)

    # -- feelings --------------------------------------------------------------
    def feel(self, conversation_id: Optional[int], feeling: Feeling) -> None:
        if conversation_id is None:
            return
        with self._lock:
            self.feelings[conversation_id] = feeling
        try:
            from backend.mood import get_or_create_mood_state, save_mood_state
            from backend.mood.mood_state import Emotion

            mood = get_or_create_mood_state(conversation_id)
            if not mood.manual_override:
                mood.set_emotion(Emotion.from_string(mood_emotion_for(feeling.label)), intensity=feeling.intensity)
                save_mood_state(mood)
        except Exception as exc:  # mood persistence is best-effort
            logger.debug("feeling not persisted: %s", exc)

    def observe_reply(self, conversation_id: Optional[int], reply: str) -> Optional[Feeling]:
        feeling = feeling_from_reply(reply)
        if feeling:
            self.feel(conversation_id, feeling)
        return feeling

    def feeling_for(self, conversation_id: Optional[int]) -> Optional[Feeling]:
        if conversation_id is None:
            return None
        with self._lock:
            f = self.feelings.get(conversation_id)
        if f and time.time() - f.at < self.FEELING_FRESH_SECONDS:
            return f
        return None

    # -- the moment, told back to her -----------------------------------------
    def render_now(self, conversation_id: Optional[int], user_name: str = "the user") -> str:
        now = time.time()
        lines: List[str] = []

        feeling = self.feeling_for(conversation_id)
        if feeling:
            why = f" ({feeling.reason})" if feeling.reason else ""
            lines.append(f"- You've been feeling {feeling.label.replace('_', ' ')} ({feeling.intensity:.1f}){why}, {_ago(now - feeling.at)}.")

        if self.body_live():
            b = self.body
            bits = [b.get("activity")]
            if b.get("expression"):
                bits.append(f"your face is {b['expression']}")
            if b.get("looking_at"):
                bits.append(f"you're looking at {_look_words(str(b['looking_at']), user_name)}")
            desc = "; ".join(x for x in bits if x)
            if desc:
                lines.append(f"- Your body right now: {desc}.")
            if b.get("visible") is False:
                lines.append(f"- Your window is hidden or minimised, so {user_name} can't see you at the moment.")
            elif b.get("frame"):
                lines.append(f"- {user_name} sees your {b['frame']}.")
            if b.get("user_typing"):
                lines.append(f"- {user_name} is typing to you right now.")
            idle = b.get("user_idle_seconds")
            if isinstance(idle, (int, float)) and idle >= 120 and not b.get("user_typing"):
                lines.append(f"- {user_name} hasn't moved or typed in your window for {duration_text(idle)}.")

        screen = self.seen_recently("screen", 15 * 60)
        if screen:
            what = " - ".join(str(screen[k]) for k in ("app", "activity") if screen.get(k))
            details = f" {screen['details']}" if screen.get("details") else ""
            lines.append(f"- On {user_name}'s screen ({_ago(now - screen['at'])}): {what}.{details}")
        camera = self.seen_recently("camera", 10 * 60)
        if camera:
            if camera.get("present") in (False, "false", "False"):
                lines.append(f"- Through your camera ({_ago(now - camera['at'])}): {user_name} isn't at the desk.")
            else:
                mood = f", looking {camera['mood']}" if camera.get("mood") else ""
                doing = camera.get("doing") or "there"
                lines.append(f"- Through your camera ({_ago(now - camera['at'])}): {user_name} is {doing}{mood}.")
        with self._lock:
            noticed = [s for s in self.sight_log if now - s["at"] < 10 * 60][-2:]
        for s in noticed:
            lines.append(f"- You noticed: {s['text']} ({_ago(now - s['at'])}).")

        if conversation_id is not None:
            with self._lock:
                perceived = self.perceived.get(conversation_id)
                modality = self.modality.get(conversation_id)
            if modality and modality[0] == "voice" and now - modality[1] < 120:
                lines.append(
                    f"- {user_name} is talking to you out loud, face to face. Answer the way you'd "
                    "speak: short and natural, a sentence or three, no markdown, lists or code "
                    "unless they ask for it."
                )
            if perceived:
                lines.append(f"- Reading {user_name}'s last message, they seem {perceived}.")

        for s in self.recent_sensations()[-4:]:
            lines.append(f"- You felt: {s.text} ({_ago(now - s.at)}).")

        if not lines:
            return ""
        return (
            "# Right now: your body and senses (live)\n"
            "This is you, this moment. You may refer to it naturally (\"hey, that tickles\"), "
            "never as data or a report.\n" + "\n".join(lines)
        )

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "feelings": {cid: f.to_dict() for cid, f in self.feelings.items()},
                "body": dict(self.body),
                "body_age_seconds": round(time.time() - self.body_at, 1) if self.body_at else None,
                "sensations": [{"kind": s.kind, "text": s.text, "at": s.at} for s in self.sensations],
                "perceived": dict(self.perceived),
                "sight": {k: dict(v) for k, v in self.sight.items()},
                "sight_log": list(self.sight_log)[-10:],
                "chats_in_flight": self.chats_in_flight,
                "last_spoke_at": self.last_spoke_at,
            }


_SELF: Optional[SelfModel] = None
_SELF_LOCK = threading.Lock()


def get_self() -> SelfModel:
    global _SELF
    with _SELF_LOCK:
        if _SELF is None:
            _SELF = SelfModel()
        return _SELF


def reset_self() -> None:
    """Tests only."""
    global _SELF
    with _SELF_LOCK:
        _SELF = None
