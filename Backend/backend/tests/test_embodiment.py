"""Embodiment: Sarah's mind and body as one self (backend/embodiment)."""
import asyncio
import time

import pytest

import backend.mood as mood_pkg
from backend.embodiment import self_model as sm
from backend.embodiment import impulse
from backend.embodiment.self_model import (
    Feeling,
    feeling_from_reply,
    get_self,
    is_silent,
    mood_emotion_for,
    parse_feel,
    reset_self,
    strip_body_tags,
)


@pytest.fixture(autouse=True)
def fresh_self(monkeypatch):
    """New self per test; mood persistence stubbed (never the real DB)."""
    reset_self()
    saved = []

    class FakeMood:
        manual_override = False

        def set_emotion(self, emotion, intensity=None):
            self.emotion, self.intensity = emotion, intensity

    monkeypatch.setattr(mood_pkg, "get_or_create_mood_state", lambda cid: FakeMood())
    monkeypatch.setattr(mood_pkg, "save_mood_state", lambda m: saved.append(m))
    yield saved
    reset_self()


# --- feelings ----------------------------------------------------------------

def test_parse_feel_forms():
    f = parse_feel("<feel>happy:0.7 | Zero is back</feel>Hey!")
    assert (f.label, f.intensity, f.reason) == ("happy", 0.7, "Zero is back")
    assert parse_feel("<feel>Worried 0.4</feel>").label == "worried"
    assert parse_feel("<feel>worried 0.4</feel>").intensity == 0.4
    assert parse_feel("<feel>excited:9</feel>").intensity == 1.0
    assert parse_feel("<feel>shy</feel>").intensity == 0.5
    assert parse_feel("<feel></feel>") is None
    assert parse_feel("no tag here") is None


def test_feeling_falls_back_to_first_face():
    f = feeling_from_reply("Oh! <face>surprised:0.8</face> Really?")
    assert (f.label, f.intensity) == ("surprised", 0.8)
    assert feeling_from_reply("plain text") is None


def test_silence_and_stripping():
    assert is_silent("<feel>calm:0.3</feel><silent/>")
    assert is_silent("<feel>calm:0.3</feel> ...")
    assert not is_silent("<feel>happy:0.6</feel>Hehe, that tickles!")
    assert strip_body_tags("<feel>happy:0.6 | x</feel>Hi <gesture>wave</gesture>there") == "Hi there"


def test_feeling_is_hers_and_persists(fresh_self):
    me = get_self()
    me.observe_reply(5, "<feel>shy:0.6 | head pat</feel>Eh?!")
    assert me.feeling_for(5).label == "shy"
    assert fresh_self and fresh_self[-1].emotion.value == "happy"  # shy -> happy mood family
    assert mood_emotion_for("annoyed") == "frustrated"
    assert mood_emotion_for("unknown-word") == "neutral"
    # Stale feelings stop being "current".
    me.feelings[5].at = time.time() - 3 * 3600
    assert me.feeling_for(5) is None


def test_derive_emotion_reports_her_felt_label():
    from backend.sarah_core import SarahCore

    get_self().feel(9, Feeling("worried", 0.4))
    assert SarahCore._derive_emotion(object(), 9) == ("worried", 0.4)


# --- the moment told back to her ------------------------------------------------

def test_render_now_describes_body_and_senses():
    me = get_self()
    assert me.render_now(1, "Zero") == ""
    me.feel(1, Feeling("happy", 0.7, "Zero is back"))
    me.update_body({
        "activity": "standing beside the chat, idle", "expression": "smiling",
        "looking_at": "user", "frame": "upper body", "visible": True,
        "user_typing": True, "user_idle_seconds": 0, "junk": {"nested": 1},
    })
    me.perceive_user(1, "frustrated")
    me.sense("touch", "Zero patted your head")
    text = me.render_now(1, "Zero")
    assert text.startswith("# Right now")
    for piece in ("feeling happy (0.7) (Zero is back)", "your face is smiling", "looking at Zero",
                  "Zero sees your upper body", "Zero is typing to you right now",
                  "they seem frustrated", "You felt: Zero patted your head"):
        assert piece in text, piece
    assert "junk" not in me.body


def test_render_now_hidden_window_and_stale_body():
    me = get_self()
    me.update_body({"activity": "idle", "visible": False, "user_idle_seconds": 900})
    text = me.render_now(1, "Zero")
    assert "can't see you" in text and "15 minutes" in text
    me.body_at = time.time() - 600  # renderer went quiet: don't claim a body state
    assert me.render_now(1, "Zero") == ""


def test_context_builder_includes_the_moment(tmp_path):
    import sqlite3
    from backend.memory.config import MemoryConfig
    from backend.memory.context_builder import ContextBuilder
    from backend.memory.memory_store import MemoryStore

    db = tmp_path / "c.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE conversations (id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT)")
    conn.execute("CREATE TABLE messages (id INTEGER PRIMARY KEY AUTOINCREMENT, conversation_id INTEGER,"
                 " role TEXT, content TEXT, meta_json TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP)")
    conn.execute("INSERT INTO conversations (title) VALUES ('t')")
    conn.commit()
    conn.close()
    config = MemoryConfig(total_token_budget=6000, llm_max_completion_tokens=1000, chars_per_token=3.5, debug_memory=False)
    ctx = ContextBuilder(store=MemoryStore(db_path=db, config=config), config=config)

    get_self().sense("touch", "Zero patted your head")
    system = ctx.build(1, "did you feel that?", process_mood=False).messages[0]["content"]
    assert "# Right now" in system and "Zero patted your head" in system
    assert system.rstrip().endswith("ago).") or "just now" in system


# --- perceiving the user -----------------------------------------------------------

def test_user_signals_are_perceived_not_adopted():
    from backend.mood.mood_signals import perceive_user_message
    from backend.mood.mood_state import Emotion, MoodState

    mood = MoodState(conversation_id=1, emotion=Emotion.NEUTRAL, intensity=0.2)
    mood, seen = perceive_user_message(mood, "This is still not working, wtf")
    assert seen == "frustrated"
    assert mood.emotion == Emotion.NEUTRAL  # her feeling isn't overwritten by theirs
    _, seen = perceive_user_message(mood, "I got the job offer!!")
    assert seen != "frustrated"  # "!!" is excitement, not exasperation


# --- body -> mind: sensations and spontaneous speech ------------------------------------

@pytest.fixture
def speak_calls(monkeypatch):
    calls = []

    async def fake_speak(kind, sensed, cid):
        calls.append((kind, sensed, cid))
        return {"reply": "<feel>shy:0.6</feel>Hey, that tickles!", "emotion": "shy", "emotion_intensity": 0.6,
                "assistant_message_id": 42, "spontaneous": kind}

    monkeypatch.setattr(impulse, "_speak", fake_speak)
    monkeypatch.setattr(impulse, "_voice_enabled", lambda: True)
    monkeypatch.setattr(impulse, "_cooldown_seconds", lambda: 40)
    monkeypatch.setattr(impulse, "get_user_name", lambda: "Zero")
    return calls


def run(coro):
    return asyncio.run(coro)


def test_touch_is_felt_and_may_be_answered(speak_calls):
    out = run(impulse.handle_event("touch", {"region": "head"}, 3))
    assert out["spoke"] and out["reply"]["assistant_message_id"] == 42
    assert speak_calls[0][1] == "Zero patted your head"
    assert get_self().recent_sensations("touch")[0].text == "Zero patted your head"


def test_cooldown_busy_and_no_conversation(speak_calls):
    run(impulse.handle_event("touch", {"region": "body"}, 3))
    again = run(impulse.handle_event("touch", {"region": "head"}, 3))
    assert not again["spoke"] and again["reason"] == "cooldown"
    assert "2 times" not in again["sensed"]
    third = run(impulse.handle_event("touch", {"region": "head"}, 3))
    assert "3 times" in third["sensed"]  # she notices being poked repeatedly

    me = get_self()
    me.last_spoke_at = 0
    me.chat_started()
    assert run(impulse.handle_event("touch", {}, 3))["reason"] == "busy talking"
    me.chat_finished()
    me.last_chat_started = 0
    assert run(impulse.handle_event("touch", {}, None))["reason"] == "no conversation"
    assert len(speak_calls) == 1


def test_return_and_arrival_thresholds(speak_calls, monkeypatch):
    assert run(impulse.handle_event("returned", {"away_seconds": 30}, 1))["sensed"] is None
    short = run(impulse.handle_event("returned", {"away_seconds": 300}, 1))
    assert short["sensed"] and short["reason"] == "not worth words"
    long_away = run(impulse.handle_event("returned", {"away_seconds": 1500}, 1))
    assert long_away["spoke"] and "25 minutes" in speak_calls[-1][1]

    get_self().last_spoke_at = 0
    monkeypatch.setattr(impulse, "_last_message_gap_seconds", lambda: 600.0)
    assert run(impulse.handle_event("arrived", {}, 1))["reason"] == "not worth words"
    monkeypatch.setattr(impulse, "_last_message_gap_seconds", lambda: 5 * 3600.0)
    assert run(impulse.handle_event("arrived", {}, 1))["spoke"]


def test_voice_can_be_turned_off(speak_calls, monkeypatch):
    monkeypatch.setattr(impulse, "_voice_enabled", lambda: False)
    out = run(impulse.handle_event("touch", {"region": "head"}, 1))
    assert out["sensed"] and not out["spoke"] and out["reason"] == "voice disabled"
    assert speak_calls == []


def test_speak_drops_silence_and_interrupted_lines(monkeypatch):
    """The real _speak: silence isn't saved; a line is dropped if the user
    started a turn while it was being written."""
    saved = []

    class Resp:
        finish_reason = "stop"

        def __init__(self, content):
            self.content = content

    class Client:
        def __init__(self, content, interrupt=False):
            self.content, self.interrupt = content, interrupt

        async def chat(self, **kw):
            assert kw["save_messages"] is False and kw["save_user_message"] is False
            if self.interrupt:
                get_self().chat_started()
            return Resp(self.content)

    class Sarah:
        memory_enabled = True

        def __init__(self, client):
            self._openrouter = client

        def _derive_emotion(self, cid):
            return ("shy", 0.6)

    import backend.models.core as core
    import backend.state as state_mod

    monkeypatch.setattr(core, "add_message", lambda *a, **k: saved.append(a) or 7)
    monkeypatch.setattr(impulse, "get_user_name", lambda: "Zero")

    monkeypatch.setattr(state_mod, "get_sarah", lambda: Sarah(Client("<feel>calm:0.3</feel><silent/>")))
    assert run(impulse._speak("touch", "Zero patted your head", 1)) is None
    assert get_self().feeling_for(1).label == "calm"  # she still felt something

    monkeypatch.setattr(state_mod, "get_sarah", lambda: Sarah(Client("<feel>shy:0.6</feel>Hey!", interrupt=True)))
    assert run(impulse._speak("touch", "Zero patted your head", 1)) is None
    get_self().chat_finished()

    monkeypatch.setattr(state_mod, "get_sarah", lambda: Sarah(Client("<feel>shy:0.6</feel>Hey, that tickles!")))
    out = run(impulse._speak("touch", "Zero patted your head", 1))
    assert out["assistant_message_id"] == 7 and out["emotion"] == "shy"
    assert saved and saved[-1][1] == "assistant"
