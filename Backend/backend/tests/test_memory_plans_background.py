"""Episodic memory, the planner/executor, and her own time in the background."""
import asyncio
import json
import time
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from backend.agency import mind as mind_mod, plans, tools
from backend.embodiment.self_model import get_self, reset_self
from backend.memory import episodic
from backend.models import core
from backend.tests.test_agency import FakeStream, _chunk, _tc


@pytest.fixture(autouse=True)
def fresh_self():
    reset_self()
    conn = episodic._conn()  # the test DB is shared: start each test with no episodes
    try:
        conn.execute("DELETE FROM episodes")
        conn.commit()
    finally:
        conn.close()
    from backend.tests.conftest import fake_embed
    episodic.set_embedder(fake_embed)
    yield
    reset_self()


def run(coro):
    return asyncio.run(coro)


def _conversation() -> int:
    conn = core.get_connection()
    try:
        cur = conn.execute("INSERT INTO conversations (title) VALUES ('t')")
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def _exchange(conv, said, reply):
    core.add_message(conv, "user", said)
    return core.add_message(conv, "assistant", reply)


# --- episodic memory -------------------------------------------------------------

def test_exchanges_become_moments_found_by_meaning():
    conv = _conversation()
    boss = _exchange(conv, "I finally beat Margit after twenty tries", "<feel>excited:0.9</feel>Twenty tries! Huge.")
    _exchange(conv, "my python config loader throws KeyError", "Check the key spelling in settings.")
    _exchange(conv, "making garlic pasta tonight", "Save me a plate.")

    found = episodic.search("did I beat Margit yet", k=3)
    assert found and found[0]["source"] == f"msg:{boss}"
    assert "Margit" in found[0]["text"] and "Twenty tries" in found[0]["text"]
    assert "<feel>" not in found[0]["text"]          # her body tags aren't memories

    # Already in the conversation window -> not "brought back" again.
    assert all(f["source"] != f"msg:{boss}" for f in
               episodic.search("did I beat Margit yet", exclude_sources={f"msg:{boss}"}))
    # A deleted message is forgotten.
    core.delete_message(boss)
    assert all(f["source"] != f"msg:{boss}" for f in episodic.search("did I beat Margit yet"))


def test_facts_experiences_and_prompt_block():
    from backend.memory import journal

    core.add_memory("assistant", "Zero's sister Mia lives in Osaka", tags="sarah", importance=1)
    journal.experience("noticed", "Zero was stuck on the Radahn fight in Elden Ring")
    journal.experience("felt", "felt calm")  # feelings stay in the journal only
    block = episodic.render_for_prompt("how is Mia doing in Osaka")
    assert block.startswith("# Moments this brings back") and "Mia lives in Osaka" in block
    assert "Radahn" in episodic.render_for_prompt("that Radahn fight in Elden Ring")
    assert episodic.render_for_prompt("quantum chromodynamics lecture") == ""
    assert not [e for e in episodic.on_day(datetime.now().date().isoformat()) if e["text"] == "felt calm"]


def test_repeated_moments_are_one_memory():
    for _ in range(3):
        episodic.note("sensed", "Zero just opened the app and you've come to")
    episodic.note("sensed", "Zero patted your head")
    assert [e["text"] for e in episodic.on_day(datetime.now().date().isoformat())].count(
        "Zero just opened the app and you've come to") == 1
    # Older duplicates and memories of deleted messages are tidied away.
    conn = episodic._conn()
    try:
        conn.execute("INSERT INTO episodes (at, kind, text, source) VALUES ('2026-01-01T10:00:00', 'sensed', "
                     "'Zero patted your head', NULL)")
        conn.execute("INSERT INTO episodes (at, kind, text, source) VALUES ('2026-01-01T10:00:00', 'conversation', "
                     "'gone', 'msg:999999')")
        conn.commit()
    finally:
        conn.close()
    assert episodic._tidy() == 2


def test_recall_tool_by_meaning_and_by_day():
    conv = _conversation()
    _exchange(conv, "the deploy script failed on the staging server", "The SSH key expired; renew it.")
    out = run(tools.call("recall", {"query": "what broke the staging deploy"}))
    assert out["ok"] and "SSH key expired" in out["result"]
    today = run(tools.call("recall", {"day": "today", "query": "deploy"}))
    assert "staging" in today["result"]
    assert "anything" in run(tools.call("recall", {"day": "2001-01-01"}))["result"]


def test_when_reads_naturally():
    now = datetime(2026, 9, 28, 15, 0)
    assert episodic.when("2026-09-28T09:10:00", now).startswith("earlier today")
    assert episodic.when("2026-09-27T20:00:00", now) == "yesterday evening"
    assert episodic.when("2026-09-24T10:00:00", now).startswith("4 days ago")


def test_context_block_skips_what_is_already_in_view():
    from backend.memory.context_builder import ContextBuilder

    conv = _conversation()
    old = _exchange(conv, "the Tarnished armor set looks great", "It really suits your build.")
    recent = [{"id": old, "role": "assistant", "content": "It really suits your build."}]
    cb = ContextBuilder.__new__(ContextBuilder)
    assert cb._episodic_block("what about the Tarnished armor set", recent, kept=1) == ""
    assert "Tarnished armor" in cb._episodic_block("what about the Tarnished armor set", recent, kept=0)


# --- plans ------------------------------------------------------------------------

def test_plan_lifecycle():
    p = plans.create("Top 5 HN stories saved to a file", ["open HN", "extract top 5", "write hn.txt"])
    assert plans.next_step(p) == 0 and "Next: step 1: open HN" in plans.status_line(p)
    p = plans.update(None, 1, "done", "loaded")
    p = plans.update(None, 2, "failed", "selector changed")
    assert "a step just failed" in plans.status_line(p)
    p = plans.update(None, add_steps=["extract with browser tables instead"])
    assert [s["text"] for s in p["steps"]][2] == "extract with browser tables instead"
    p = plans.update(None, 3, "done", "table read ok")
    p = plans.update(None, 4, "done", "hn.txt has 5 lines")
    assert p["status"] == "done" and "all finished" in plans.status_line(p)
    assert plans.open_plans() == []


def test_done_needs_evidence():
    plans.create("Open YouTube", ["open youtube", "search lofi"])
    with pytest.raises(ValueError, match="what you checked"):
        plans.update(None, 1, "done")


def test_blocked_plans_wait_and_show_in_her_moment():
    p = plans.create("Log into the router", ["open router page", "sign in"])
    plans.update(p["id"], 1, "done", "it loaded")
    plans.update(p["id"], plan_status="blocked", note="need Zero's router password")
    assert "blocked" in plans.status_line(plans.get(p["id"]))
    assert "Your open plans" in get_self().render_now(1, "Zero")
    m = mind_mod.Mind()
    assert m.plan_to_continue(time.time() + 3600) is None      # blocked: not picked up


def test_plan_tools_render_a_checklist():
    out = run(tools.call("make_plan", {"goal": "Tidy downloads", "steps": ["list files", "move installers"]}))
    assert out["ok"] and "[ ] 1. list files" in out["result"]
    out = run(tools.call("update_plan", {"step": 1, "status": "done", "note": "42 files"}))
    assert "[x] 1. list files (42 files)" in out["result"]


def test_executor_extends_the_budget_while_a_plan_is_open(monkeypatch):
    from backend.memory import openrouter_client as oc

    offered, seen = [], []

    class Completions:
        def create(self, **kw):
            offered.append(bool(kw.get("tools")))
            seen.append(kw["messages"])
            n = len(offered)
            if not kw.get("tools"):
                return FakeStream([_chunk("Got through most of it.", finish="stop")])
            if n == 1:
                args = json.dumps({"goal": "big job", "steps": [f"part {i}" for i in range(1, 6)]})
                return FakeStream([_chunk(tool_calls=[_tc(0, "p", "make_plan", args)], finish="tool_calls")])
            return FakeStream([_chunk(tool_calls=[_tc(0, f"c{n}", "list_my_tools", "{}")], finish="tool_calls")])

    client = oc.OpenRouterClient.__new__(oc.OpenRouterClient)
    client._client = SimpleNamespace(chat=SimpleNamespace(completions=Completions()))
    client.config = SimpleNamespace(llm_max_completion_tokens=100, llm_temperature=0.5, chars_per_token=4)
    client._is_local_mode = lambda: False
    packet = SimpleNamespace(messages=[{"role": "user", "content": "do the big job"}], estimated_tokens=10, debug_info={})
    client._prepare_turn = lambda *a, **k: (packet, 1)
    client._finish_turn = lambda **kw: SimpleNamespace(content=kw["raw_content"])
    monkeypatch.setattr(oc.llm_models, "completion_kwargs", lambda: {"model": "fake:free", "extra_body": None})
    monkeypatch.setattr(tools, "_custom_tools", lambda: {})

    async def collect():
        return [e async for e in client.chat_stream(1, "do the big job")]

    events = run(collect())
    # 5 steps -> budget 4 + 3*5 = 19 tool rounds, then one without tools.
    assert offered == [True] * 19 + [False]
    plan_events = [e for e in events if e["type"] == "tool" and e.get("plan")]
    assert plan_events and plan_events[0]["plan"]["goal"] == "big job"
    # After a non-plan action she's reminded of the next step.
    assert "Next: step 1: part 1" in seen[2][-1]["content"]
    # Out of steps with the plan open: told it stays open.
    assert "plan stays open" in seen[-1][-1]["content"]
    assert events[-1]["response"].content == "Got through most of it."


def test_she_cant_wrap_up_with_plan_steps_left(monkeypatch):
    from backend.memory import openrouter_client as oc

    seen, n = [], [0]

    class Completions:
        def create(self, **kw):
            n[0] += 1
            seen.append(kw["messages"])
            if n[0] == 1:
                args = json.dumps({"goal": "YouTube lofi", "steps": ["open youtube", "search lofi"]})
                return FakeStream([_chunk(tool_calls=[_tc(0, "p", "make_plan", args)], finish="tool_calls")])
            if n[0] == 2:
                return FakeStream([_chunk("All done!", finish="stop")])       # the shortcut
            return FakeStream([_chunk("Actually not yet: I couldn't search.", finish="stop")])

    client = oc.OpenRouterClient.__new__(oc.OpenRouterClient)
    client._client = SimpleNamespace(chat=SimpleNamespace(completions=Completions()))
    client.config = SimpleNamespace(llm_max_completion_tokens=100, llm_temperature=0.5, chars_per_token=4)
    client._is_local_mode = lambda: False
    packet = SimpleNamespace(messages=[{"role": "user", "content": "play lofi"}], estimated_tokens=10, debug_info={})
    client._prepare_turn = lambda *a, **k: (packet, 1)
    client._finish_turn = lambda **kw: SimpleNamespace(content=kw["raw_content"])
    monkeypatch.setattr(oc.llm_models, "completion_kwargs", lambda: {"model": "fake:free", "extra_body": None})
    monkeypatch.setattr(tools, "_custom_tools", lambda: {})

    async def collect():
        return [e async for e in client.chat_stream(1, "play lofi")]

    events = run(collect())
    assert "Not finished yet" in seen[2][-1]["content"] and "open youtube" in seen[2][-1]["content"]
    assert "couldn't search" in events[-1]["response"].content
    assert n[0] <= 5  # nudged at most twice


# --- background / her own time ------------------------------------------------------

def _client_saying(text, prompts):
    class Client:
        async def chat_stream(self, **kw):
            prompts.append(kw["user_message"])
            yield {"type": "done", "response": SimpleNamespace(finish_reason="stop", content=text)}
    return Client()


def test_tray_means_away_and_own_time_is_kept_for_later(monkeypatch):
    import backend.state as state_mod
    from backend.agency import senses as senses_mod

    me = get_self()
    me.last_conversation_id = 4
    prompts, pushed = [], []
    monkeypatch.setattr(state_mod, "get_sarah", lambda: SimpleNamespace(_openrouter=_client_saying(
        "<feel>content:0.6</feel>While you were away, I read up on Radahn's second phase and saved the tips "
        "to notes/radahn.md.", prompts)))

    async def push(msg):
        pushed.append(msg)
        return True

    monkeypatch.setattr(senses_mod.senses, "push", push)
    m = mind_mod.Mind()
    now = time.time()
    assert m.away_for(now) is None and not m.own_time_due(now)
    m.set_tray(True)
    assert m.away_for(now + 60) is not None and m.own_time_due(now + 60)
    record = run(m.tick())
    assert record["outcome"] == "kept for when Zero is back" and pushed == []
    assert "Your own time" in prompts[0] and "tray" in prompts[0]
    assert m.away_log[0]["text"].startswith("read up on Radahn")
    assert not m.own_time_due(time.time())                     # not again right away
    assert m.away_report() == ""                               # still away
    m.set_tray(False)
    assert "read up on Radahn" in m.away_report()
    # Her greeting on return carries the news (once).
    monkeypatch.setattr(mind_mod, "mind", m)
    from backend.embodiment import impulse
    sensed, speak = impulse.describe("returned", {"away_seconds": 1800})
    assert speak and "read up on Radahn" in sensed
    sensed, _ = impulse.describe("returned", {"away_seconds": 1800})
    assert "read up on Radahn" not in sensed


def test_no_sign_of_zero_for_a_while_counts_as_away():
    m = mind_mod.Mind()
    now = time.time()
    m.last_present = now - 25 * 60
    assert m.away_for(now) >= 25 * 60
    m.last_present = now - 5 * 60
    assert m.away_for(now) is None


def test_unfinished_plan_is_picked_up_and_spoken_when_zero_is_here(monkeypatch):
    import backend.state as state_mod
    from backend.agency import senses as senses_mod

    me = get_self()
    me.last_conversation_id = 4
    p = plans.create("Sort the photos", ["list", "group by month", "move"])
    plans.update(p["id"], 1, "done", "it loaded")
    data = json.loads(plans._FILE.read_text())
    data[-1]["touched"] = time.time() - 600
    plans._FILE.write_text(json.dumps(data))
    prompts, pushed = [], []
    focus_seen = []

    class Client:
        async def chat_stream(self, **kw):
            prompts.append(kw["user_message"])
            focus_seen.append(plans.focus.get())
            yield {"type": "done", "response": SimpleNamespace(finish_reason="stop",
                                                               content="Photos are grouped by month now; moving them next.")}

    monkeypatch.setattr(state_mod, "get_sarah", lambda: SimpleNamespace(_openrouter=Client(),
                                                                        _derive_emotion=lambda c: ("calm", 0.4)))
    monkeypatch.setattr(core, "add_message", lambda *a, **k: 9)

    async def push(msg):
        pushed.append(msg)
        return True

    monkeypatch.setattr(senses_mod.senses, "push", push)
    m = mind_mod.Mind()
    m.last_present = time.time()
    record = run(m.tick())
    assert record["kind"] == "plan" and record["outcome"] == "spoke"
    assert "Sort the photos" in prompts[0] and "Next: step 2: group by month" in prompts[0]
    assert focus_seen == [p["id"]] and plans.focus.get() is None
    assert pushed[-1]["type"] == "say"
    assert plans.get(p["id"])["attempts"] == 1


def test_voice_prefs(tmp_path, monkeypatch):
    from backend import tts_kokoro

    monkeypatch.setattr(tts_kokoro, "_prefs_file", lambda: tmp_path / "voice.json")
    monkeypatch.setattr(tts_kokoro, "voices", lambda: [{"id": "af_bella"}, {"id": "bf_emma"}])
    assert tts_kokoro.set_prefs("bf_emma", 1.2) == {"voice": "bf_emma", "speed": 1.2}
    assert tts_kokoro._lang(tts_kokoro.voice()) == "en-gb"
    with pytest.raises(ValueError):
        tts_kokoro.set_prefs("xx_nobody")
    assert tts_kokoro.set_prefs(speed_value=9)["speed"] == 1.4
