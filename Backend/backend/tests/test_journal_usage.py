"""Her memory of her days (journal) and the free-request usage meter."""
import asyncio
import json
from datetime import date, timedelta

import pytest

from backend import usage
from backend.memory import journal


@pytest.fixture(autouse=True)
def clean_tables():
    from backend.models.core import get_connection

    conn = journal._conn()
    try:
        for table in ("experiences", "journal"):
            conn.execute(f"DELETE FROM {table}")
        usage._ensure_table(conn)
        conn.execute("DELETE FROM llm_usage")
        conn.execute("DELETE FROM memories WHERE tags = 'journal'")
        conn.commit()
    finally:
        conn.close()
    journal._last_text.clear()
    yield


# --- experiences ------------------------------------------------------------------

def test_experiences_are_kept_and_repeats_skipped():
    journal.experience("saw", "On Creator's screen: Elden Ring - fighting Margit")
    journal.experience("saw", "On Creator's screen: Elden Ring - fighting Margit")  # same scene again
    journal.experience("did", "used web_search (query=margit weakness)")
    today = journal.experiences(date.today().isoformat())
    assert [e["kind"] for e in today] == ["saw", "did"]


def test_what_she_sees_feels_and_does_becomes_experience():
    from backend.embodiment.self_model import Feeling, get_self, reset_self

    reset_self()
    me = get_self()
    me.see({"screen": {"app": "VS Code", "activity": "debugging checkout.py"}, "notable": "a failing test"})
    me.feel(1, Feeling("worried", 0.4, "the tests fail"))
    me.feel(1, Feeling("worried", 0.5))       # same feeling: not a new experience
    me.sense("touch", "Creator patted your head")
    kinds = [e["kind"] for e in journal.experiences(date.today().isoformat())]
    assert kinds.count("felt") == 1 and "noticed" in kinds and "saw" in kinds and "sensed" in kinds
    reset_self()


# --- the journal ---------------------------------------------------------------------

def _yesterday():
    return (date.today() - timedelta(days=1)).isoformat()


def _add_experience(day, kind, text):
    conn = journal._conn()
    try:
        conn.execute("INSERT INTO experiences (at, day, kind, text) VALUES (?, ?, ?, ?)", (f"{day}T20:15:00", day, kind, text))
        conn.commit()
    finally:
        conn.close()


def test_writing_a_day_saves_entry_and_new_memories():
    day = _yesterday()
    _add_experience(day, "saw", "On Creator's screen: Elden Ring - fighting Margit, died 7 times")
    _add_experience(day, "noticed", "Creator beat Margit on the 9th try")
    prompts = []

    async def fake_complete(prompt):
        prompts.append(prompt)
        return json.dumps({"entry": "Creator finally beat Margit tonight. I cheered way too loud.",
                           "memories": ["Creator beat Margit in Elden Ring", "Creator beat Margit in Elden Ring"]})

    out = asyncio.run(journal.write_day(day, complete=fake_complete))
    assert out["entry"].startswith("Creator finally beat Margit")
    assert out["new_memories"] == 1  # duplicates aren't stored twice
    assert "died 7 times" in prompts[0] and "JSON only" in prompts[0]
    assert journal.has_entry(day)
    assert journal.days_needing_entry() == []
    block = journal.render()
    assert block.startswith("# Your recent days") and "finally beat Margit" in block


def test_a_day_with_nothing_is_not_written():
    async def never(prompt):
        raise AssertionError("no request for an empty day")

    assert asyncio.run(journal.write_day(_yesterday(), complete=never)) is None


def test_missed_days_are_caught_up():
    for back in (1, 3):
        _add_experience((date.today() - timedelta(days=back)).isoformat(), "saw", "something")
    assert journal.days_needing_entry() == [
        (date.today() - timedelta(days=1)).isoformat(),
        (date.today() - timedelta(days=3)).isoformat(),
    ]


def test_earlier_today_is_in_the_block():
    journal.experience("saw", "On Creator's screen: Minecraft - building a castle")
    assert "Earlier today:" in journal.render() and "Minecraft" in journal.render()


def test_parse_is_lenient():
    assert journal._parse('```json\n{"entry": "Nice day.", "memories": ["a"]}\n```') == {"entry": "Nice day.", "memories": ["a"]}
    assert journal._parse("Just prose.") == {"entry": "Just prose.", "memories": []}
    assert journal._parse("") == {}


# --- usage meter -------------------------------------------------------------------------

def test_usage_counts_by_category_and_flags_paid_models():
    usage.record("nvidia/nemotron-3-ultra-550b-a55b:free", 200)
    with usage.using("vision"):
        usage.record("dots-studio/dots-3-note-preview:free", 200)
        usage.record("dots-studio/dots-3-note-preview:free", 429)
    usage.record("openai/gpt-5", 200, category="chat")
    t = usage.today()
    assert t["used"] == 4 and t["by_category"] == {"chat": 2, "vision": 2}
    assert t["rate_limited"] == 1 and t["non_free_models"] == ["openai/gpt-5"]
    assert t["remaining"] == t["limit"] - 4


def test_requests_to_openrouter_are_counted_at_the_transport(monkeypatch):
    import requests
    import requests.adapters

    def fake_send(self, request, *args, **kwargs):
        resp = requests.Response()
        resp.status_code = 200
        resp._content = b"{}"
        resp.request = request
        return resp

    monkeypatch.setattr(requests.adapters.HTTPAdapter, "send", fake_send)
    monkeypatch.setattr(usage, "_installed", False)
    import httpx
    monkeypatch.setattr(httpx.Client, "send", httpx.Client.send)  # restored after the test
    usage.install()
    with usage.using("memory"):
        requests.post("https://openrouter.ai/api/v1/chat/completions", json={"model": "x/y:free"})
    requests.get("https://example.com/")  # not OpenRouter: not counted
    t = usage.today()
    assert t["used"] == 1 and t["by_category"] == {"memory": 1}
