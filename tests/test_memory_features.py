# tests/test_memory_features.py
"""Procedure memory, proactive engine, screen timeline and text matching."""
from datetime import datetime, timedelta

from backend.utils.text_similarity import similarity, tokenize


# ------------------------------------------------------------
# Text similarity
# ------------------------------------------------------------
def test_stemming_matches_word_forms():
    assert tokenize("files") == tokenize("file")
    assert tokenize("created") == tokenize("create")
    assert tokenize("running") == tokenize("run")
    assert tokenize("boxes") == tokenize("box")


def test_similarity_range():
    assert similarity("rename my photos", "rename my photos") == 1.0
    assert similarity("rename my photos", "bake a cake") == 0.0
    assert similarity("", "anything") == 0.0


# ------------------------------------------------------------
# Procedure memory (MemP-style)
# ------------------------------------------------------------
def test_procedure_build_and_retrieve(temp_db):
    from backend import procedure_memory as pm

    pid = pm.record_attempt("rename photos by date", ["list photos", "read dates", "rename"], True)
    assert pid is not None

    found = pm.find_procedures("rename my photos by their date")
    assert [p["id"] for p in found] == [pid]
    assert found[0]["steps"] == ["list photos", "read dates", "rename"]

    assert pm.find_procedures("bake a chocolate cake") == []
    assert "rename photos by date" in pm.format_for_prompt("rename photos by date")
    assert pm.format_for_prompt("bake a chocolate cake") == ""


def test_failed_attempt_without_match_is_not_saved(temp_db):
    from backend import procedure_memory as pm

    assert pm.record_attempt("launch rocket", ["press button"], False) is None
    assert pm.list_procedures() == []


def test_similar_task_updates_instead_of_duplicating(temp_db):
    from backend import procedure_memory as pm

    a = pm.record_attempt("rename photos by date", ["a"], True)
    b = pm.record_attempt("rename photos by date please", ["b"], True)
    assert a == b
    assert len(pm.list_procedures()) == 1
    assert pm.list_procedures()[0]["successes"] == 2


def test_failing_procedure_gets_retired(temp_db):
    from backend import procedure_memory as pm

    pid = pm.record_attempt("clean downloads folder", ["delete old files"], True)
    for _ in range(3):
        pm.record_feedback(pid, False)
    assert pm.find_procedures("clean downloads folder") == []
    retired = pm.list_procedures(include_retired=True)
    assert retired[0]["retired"] is True


def test_mostly_failing_procedure_steps_get_replaced(temp_db):
    from backend import procedure_memory as pm

    pid = pm.record_attempt("backup documents", ["old way"], True)
    pm.record_feedback(pid, False)  # 1 success, 1 failure
    pm.record_attempt("backup documents", ["new way"], True)
    assert pm.find_procedures("backup documents")[0]["steps"] == ["new way"]


def test_always_bad_ratings_eventually_retire(temp_db):
    # Each finished run counts a provisional success; a bad rating must undo it,
    # otherwise the rate sits at 50% forever and a bad recipe is never retired.
    from backend import procedure_memory as pm

    first = None
    for _ in range(3):
        pid = pm.record_attempt("organise my music", ["sort by artist"], True)
        first = first or pid
        assert pid == first  # the same recipe keeps being reused...
        pm.record_feedback(pid, False, correct_provisional=True)
    # ...until three bad ratings retire it
    assert pm.find_procedures("organise my music") == []
    assert pm.list_procedures(include_retired=True)[0]["retired"] is True
    # Sarah can then learn a fresh recipe from the next success
    assert pm.record_attempt("organise my music", ["sort by album"], True) != first


def test_good_rating_confirms_without_double_counting(temp_db):
    from backend import procedure_memory as pm

    pid = pm.record_attempt("make a playlist", ["pick songs"], True)
    pm.record_feedback(pid, True, correct_provisional=True)
    p = pm.list_procedures()[0]
    assert (p["successes"], p["failures"]) == (1, 0)


def test_procedure_feedback_unknown_id(temp_db):
    from backend import procedure_memory as pm
    assert pm.record_feedback(999, True) is False


# ------------------------------------------------------------
# Proactive engine (ProactiveAgent-style)
# ------------------------------------------------------------
T0 = datetime(2026, 9, 30, 12, 0, 0)


def test_first_offer_allowed_then_waits_for_answer(temp_db):
    from backend import proactive_engine as pe

    first = pe.maybe_offer("screen", "Want me to fix that error?", now=T0)
    assert first["offer"] and first["suggestion_id"]

    second = pe.maybe_offer("screen", "Need help with the build?", now=T0 + timedelta(minutes=1))
    assert not second["offer"]
    assert second["reason"] == "waiting on an earlier suggestion"


def test_cooldown_and_duplicates(temp_db):
    from backend import proactive_engine as pe

    sid = pe.maybe_offer("screen", "Want me to fix that error?", now=T0)["suggestion_id"]
    pe.record_feedback(sid, "accepted", now=T0 + timedelta(minutes=1))

    assert pe.should_offer("screen", "Something new", now=T0 + timedelta(minutes=5)) == (False, "cooldown")
    ok, reason = pe.should_offer("screen", "Want me to fix that error?", now=T0 + timedelta(minutes=20))
    assert (ok, reason) == (False, "already suggested recently")
    assert pe.should_offer("screen", "Something new", now=T0 + timedelta(minutes=20))[0]


def test_unanswered_offer_becomes_ignored(temp_db):
    from backend import proactive_engine as pe

    pe.maybe_offer("screen", "Tip one", now=T0)
    assert pe.expire_stale(now=T0 + timedelta(minutes=16)) == 1
    assert pe.stats()["screen"]["ignored"] == 1


def test_backs_off_after_rejections_and_recovers(temp_db):
    from backend import proactive_engine as pe

    t = T0
    for i in range(4):
        sid = pe.maybe_offer("music", f"Play song number {i} alpha{i}", now=t)["suggestion_id"]
        assert sid, f"offer {i} should have been allowed"
        pe.record_feedback(sid, "rejected", now=t + timedelta(minutes=1))
        t += timedelta(minutes=11)

    ok, reason = pe.should_offer("music", "Play something else entirely", now=t)
    assert not ok and "declines" in reason

    # Other categories are unaffected
    assert pe.should_offer("coding", "Want a hand with that bug?", now=t)[0]

    # A week later the rejections have aged out
    assert pe.should_offer("music", "Play something else entirely", now=t + timedelta(days=8))[0]


def test_invalid_outcome_rejected(temp_db):
    import pytest
    from backend import proactive_engine as pe

    sid = pe.maybe_offer("screen", "Tip", now=T0)["suggestion_id"]
    with pytest.raises(ValueError):
        pe.record_feedback(sid, "maybe")
    assert pe.record_feedback(12345, "accepted") is False


# ------------------------------------------------------------
# Screen timeline (StreamForest-lite)
# ------------------------------------------------------------
def test_timeline_merges_repeats(temp_db):
    from backend import screen_timeline as st

    a = st.add_event("Player is in the main menu of Minecraft")
    b = st.add_event("Player is in the main menu of Minecraft still")
    c = st.add_event("Player is fighting a zombie in a dark cave")
    assert a == b and c != a

    events = st.recent_events()
    assert len(events) == 2
    assert events[0]["repeat_count"] == 2
    assert "seen 2x" in st.build_timeline_text()
    assert st.add_event("   ") is None


def test_timeline_prunes_old_events(temp_db, monkeypatch):
    from backend import screen_timeline as st

    monkeypatch.setattr(st, "MAX_EVENTS_KEPT", 3)
    for word in ["alpha", "bravo", "charlie", "delta", "echo"]:
        st.add_event(f"{word} {word} {word}")
    events = st.recent_events(10)
    assert [e["description"].split()[0] for e in events] == ["charlie", "delta", "echo"]


def test_timeline_summary_uses_llm(temp_db):
    import asyncio
    from backend import screen_timeline as st

    prompts = []

    async def fake_llm(prompt):
        prompts.append(prompt)
        return "You beat the boss."

    assert asyncio.run(st.summarize(fake_llm, "")) == "I haven't seen anything on screen yet."
    st.add_event("Boss fight started")
    assert asyncio.run(st.summarize(fake_llm, st.build_timeline_text())) == "You beat the boss."
    assert "Boss fight started" in prompts[0]


# ------------------------------------------------------------
# Concurrency: simultaneous requests
# ------------------------------------------------------------
def test_simultaneous_offers_only_one_wins(temp_db):
    from concurrent.futures import ThreadPoolExecutor
    from backend import proactive_engine as pe

    with ThreadPoolExecutor(8) as pool:
        results = list(pool.map(lambda i: pe.maybe_offer("screen", f"Tip {i} unique{i}", now=T0), range(8)))
    assert sum(r["offer"] for r in results) == 1


def test_simultaneous_procedure_saves_dont_duplicate(temp_db):
    from concurrent.futures import ThreadPoolExecutor
    from backend import procedure_memory as pm

    with ThreadPoolExecutor(8) as pool:
        list(pool.map(lambda i: pm.record_attempt("water the plants", ["fill can", "pour"], True), range(8)))
    procs = pm.list_procedures()
    assert len(procs) == 1 and procs[0]["successes"] == 8
