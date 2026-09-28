"""Sarah's initiative (mind loop) and her agenda."""
import asyncio
import time
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from backend.agency import agenda, mind as mind_mod
from backend.embodiment.self_model import get_self, reset_self


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(agenda, "_FILE", tmp_path / "agenda.json")
    reset_self()
    yield
    reset_self()


# --- agenda ------------------------------------------------------------------

def test_agenda_tags_add_complete_and_strip():
    text = 'Good luck! <agenda add="Ask how the exam went" in="2d"/> Talk soon.'
    assert agenda.apply_tags(text) == ["added #1"]
    assert agenda.apply_tags(text) == ["added #1"]  # no duplicates
    assert agenda.strip_tags(text) == "Good luck!  Talk soon."
    item = agenda.open_items()[0]
    assert datetime.fromisoformat(item["due"]) > datetime.now() + timedelta(days=1)
    assert "#1 Ask how the exam went (due" in agenda.render()
    agenda.apply_tags('<agenda done="#1"/>')  # the way she writes it
    assert agenda.open_items() == []


def test_agenda_at_absolute_time():
    when = (datetime.now() + timedelta(hours=5)).replace(microsecond=0)
    agenda.apply_tags(f'<agenda add="Wish Zero luck" at="{when.isoformat()}"/>')
    due = datetime.fromisoformat(agenda.open_items()[0]["due"])
    assert abs((due - when).total_seconds()) < 2


def test_due_items_and_durations():
    agenda.add("now thing", timedelta(minutes=-1))
    agenda.add("later thing", timedelta(hours=3))
    assert [i["text"] for i in agenda.due_items()] == ["now thing"]
    assert agenda.parse_duration("30m") == timedelta(minutes=30)
    assert agenda.parse_duration("1.5h") == timedelta(hours=1.5)
    assert agenda.parse_duration("2d") == timedelta(days=2)
    assert agenda.parse_duration("soon") is None


def test_agenda_shows_in_her_moment():
    agenda.add("Look into cheaper flights to Osaka")
    assert "Your agenda" in get_self().render_now(1, "Zero")


# --- when she takes a moment ---------------------------------------------------------

def fresh_mind():
    m = mind_mod.Mind()
    m.last_checkin = 0
    return m


def test_she_waits_for_a_good_moment():
    m = fresh_mind()
    me = get_self()
    now = time.time()
    assert m.ready(now) == "no conversation open"
    me.last_conversation_id = 7
    assert m.ready(now) is None
    me.chat_started()
    assert m.ready(now) == "in a conversation"
    me.chat_finished()
    me.last_chat_started = now - 5
    assert m.ready(now) == "in a conversation"  # right after a turn: give it a beat
    me.last_chat_started = 0
    me.last_spoke_at = now - 30
    assert m.ready(now) == "spoke up recently"
    me.last_spoke_at = 0
    m.quiet = True
    assert m.ready(now) == "quiet"


def test_triggers_notable_sight_due_agenda_and_long_silence():
    m = fresh_mind()
    me = get_self()
    now = time.time()
    assert m.triggers(now) == []
    me.see({"notable": "A boss fight just started in Elden Ring"})
    assert any("boss fight" in t for t in m.triggers(time.time()))
    m.last_notable = "A boss fight just started in Elden Ring"
    assert not any("boss fight" in t for t in m.triggers(time.time()))  # not twice
    agenda.add("Remind Zero to stretch", timedelta(minutes=-1))
    assert any("agenda #1" in t for t in m.triggers(time.time()))
    me.see({"camera": {"present": True, "doing": "gaming"}})
    me.last_chat_started = time.time() - 3600
    assert any("haven't talked for 60 minutes" in t for t in m.triggers(time.time()))


def test_a_moment_that_speaks_is_saved_and_pushed(monkeypatch):
    import backend.models.core as core
    import backend.state as state_mod
    from backend.agency import senses as senses_mod

    me = get_self()
    me.last_conversation_id = 3
    pushed, saved = [], []

    class Client:
        async def chat_stream(self, **kw):
            assert kw["save_messages"] is False and kw["save_user_message"] is False
            yield {"type": "tool", "status": "start", "name": "web_search", "args": {}}
            yield {"type": "done", "response": SimpleNamespace(
                finish_reason="stop",
                content='<feel>excited:0.7 | boss fight</feel>Ooh, that boss! Dodge left. '
                        '<agenda add="Ask if they beat the boss" in="30m"/>')}

    sarah = SimpleNamespace(_openrouter=Client(), _derive_emotion=lambda cid: ("excited", 0.7))
    monkeypatch.setattr(state_mod, "get_sarah", lambda: sarah)
    monkeypatch.setattr(core, "add_message", lambda *a, **k: saved.append(a) or 11)

    async def push(msg):
        pushed.append(msg)
        return True

    monkeypatch.setattr(senses_mod.senses, "push", push)
    record = asyncio.run(fresh_mind().think(["your eyes just caught: a boss fight"]))
    assert record["outcome"] == "spoke"
    assert saved[0][:2] == (3, "assistant")
    assert [p["type"] for p in pushed] == ["activity", "say"]
    assert pushed[-1]["reply"]["assistant_message_id"] == 11
    assert agenda.open_items()[0]["text"] == "Ask if they beat the boss"
    assert me.feeling_for(3).label == "excited"


def test_filler_counts_as_silence():
    from backend.embodiment import is_silent

    for filler in ("I'm here, Creator.", "<feel>calm:0.3</feel>I'm here.", "Let me know if you need anything!", ""):
        assert is_silent(filler), filler
    assert not is_silent("<feel>curious:0.6</feel>How did the interview go this morning?")


def test_when_its_been_quiet_she_starts_a_conversation(monkeypatch):
    import backend.state as state_mod
    import backend.models.core as core
    from backend.agency import senses as senses_mod

    me = get_self()
    me.last_conversation_id = 3
    prompts = []

    class Client:
        async def chat_stream(self, **kw):
            prompts.append(kw["user_message"])
            yield {"type": "done", "response": SimpleNamespace(
                finish_reason="stop", content="<feel>curious:0.6</feel>Still on that checkout bug? What was the fix in the end?")}

    monkeypatch.setattr(state_mod, "get_sarah", lambda: SimpleNamespace(_openrouter=Client(), _derive_emotion=lambda c: ("curious", 0.6)))
    monkeypatch.setattr(core, "add_message", lambda *a, **k: 5)

    async def push(msg):
        return True

    monkeypatch.setattr(senses_mod.senses, "push", push)
    m = fresh_mind()
    me.see({"screen": {"app": "VS Code", "activity": "coding"}})  # someone's at the PC
    me.last_chat_started = time.time() - 20 * 60
    found = m.triggers(time.time())
    assert any("haven't talked for 20 minutes" in t for t in found)
    record = asyncio.run(m.think(found))
    assert record["outcome"] == "spoke"
    assert "Start a real conversation" in prompts[0] and "No filler" in prompts[0]
    assert m.triggers(time.time()) == []  # not again right away


def test_an_empty_moment_is_not_sent(monkeypatch):
    import backend.state as state_mod

    get_self().last_conversation_id = 3

    class Client:
        async def chat_stream(self, **kw):  # the chat layer's fallback for an empty reply
            yield {"type": "done", "response": SimpleNamespace(finish_reason="stop", content="I'm here, Creator.")}

    monkeypatch.setattr(state_mod, "get_sarah", lambda: SimpleNamespace(_openrouter=Client()))
    assert asyncio.run(fresh_mind().think(["your eyes just caught: x"]))["outcome"] == "stayed quiet"


def test_a_quiet_moment_says_nothing(monkeypatch):
    import backend.state as state_mod

    get_self().last_conversation_id = 3

    class Client:
        async def chat_stream(self, **kw):
            yield {"type": "done", "response": SimpleNamespace(finish_reason="stop",
                                                               content="<feel>calm:0.3</feel><silent/>")}

    monkeypatch.setattr(state_mod, "get_sarah", lambda: SimpleNamespace(_openrouter=Client()))
    record = asyncio.run(fresh_mind().think(["your eyes just caught: Zero is typing code"]))
    assert record["outcome"] == "stayed quiet"
