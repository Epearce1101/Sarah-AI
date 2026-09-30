# tests/test_durable_tasks.py
"""
Durable background tasks (DBOS).

Includes two real crash tests: the backend process is killed mid-task and a
fresh process must pick the task back up.
"""
import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

pytest.importorskip("dbos")

from backend import db  # noqa: E402

HERE = Path(__file__).resolve().parent
WORKER = HERE / "durable_crash_worker.py"


class FakeLLM:
    def __init__(self):
        self.prompts = []
        self.fail_next = 0
        self.reply_override = None
        self.reply_queue = []  # replies to give first, one per call

    def __call__(self, prompt):
        self.prompts.append(prompt)
        if self.fail_next:
            self.fail_next -= 1
            raise ConnectionError("Groq hiccup")
        if self.reply_queue:
            return self.reply_queue.pop(0)
        if self.reply_override is not None:
            return self.reply_override
        return "STEPS:\n1. Find the files\n2. Rename them\nRESULT:\nRenamed 12 photos."


@pytest.fixture(scope="module")
def durable(tmp_path_factory):
    data_dir = tmp_path_factory.mktemp("durable")
    old = (db.DATA_DIR, db.DB_PATH)
    db.DATA_DIR, db.DB_PATH = data_dir, data_dir / "test.db"
    db.init_db()

    from backend import durable_tasks, procedure_memory
    procedure_memory.init_tables()
    durable_tasks.init_tables()

    llm = FakeLLM()
    durable_tasks.configure(llm, data_dir / "durable.sqlite")
    durable_tasks.launch()
    yield durable_tasks, llm
    durable_tasks.shutdown()
    db.DATA_DIR, db.DB_PATH = old


def wait_for(durable_tasks, workflow_id, status=None, state=None, timeout=30):
    deadline = time.time() + timeout
    while time.time() < deadline:
        info = durable_tasks.get_task_status(workflow_id)
        if info and (status is None or info["status"] == status) and (state is None or info["state"] == state):
            return info
        time.sleep(0.1)
    raise AssertionError(f"{workflow_id} never reached status={status} state={state}: {info}")


def task_row(task_id):
    conn = db.get_connection()
    try:
        return dict(conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone())
    finally:
        conn.close()


# ------------------------------------------------------------
# Parsing (no DBOS needed)
# ------------------------------------------------------------
def test_parse_reply():
    from backend.durable_tasks import parse_reply

    steps, result = parse_reply("STEPS:\n1. a\n2) b\n- c\nRESULT:\nfinal answer")
    assert steps == ["a", "b", "c"] and result == "final answer"
    assert parse_reply("just some text") == ([], "just some text")
    assert parse_reply("") == ([], "")


# ------------------------------------------------------------
# Full workflow
# ------------------------------------------------------------
def test_task_runs_and_reports_back(durable):
    dt, llm = durable
    started = dt.start_background_task("Rename my photos by date", "They're in Pictures/2025")
    info = wait_for(dt, started["workflow_id"], status="SUCCESS")

    out = info["output"]
    assert out["result"] == "Renamed 12 photos."
    assert out["steps"] == ["Find the files", "Rename them"]
    assert out["procedure_id"] is not None
    assert info["state"] == "done"

    row = task_row(started["task_id"])
    assert row["status"] == "done" and row["result_summary"] == "Renamed 12 photos."

    notes = dt.get_notifications()
    mine = [n for n in notes if n["workflow_id"] == started["workflow_id"]]
    assert len(mine) == 1 and "Rename my photos by date" in mine[0]["message"]
    assert dt.mark_notification_read(mine[0]["id"])
    assert all(n["id"] != mine[0]["id"] for n in dt.get_notifications())

    listed = {t["workflow_id"]: t for t in dt.list_tasks()}
    assert listed[started["workflow_id"]]["task_id"] == started["task_id"]


def test_second_similar_task_reuses_procedure(durable):
    dt, llm = durable
    first = dt.start_background_task("Rename my photos by date", "Pictures/2025")
    wait_for(dt, first["workflow_id"], status="SUCCESS")
    started = dt.start_background_task("Rename my photos by their date", "Now the 2026 folder")
    wait_for(dt, started["workflow_id"], status="SUCCESS")
    prompt = next(p for p in reversed(llm.prompts) if "Now the 2026 folder" in p)
    assert "Procedures that worked for similar tasks before" in prompt
    assert "Find the files" in prompt


def test_feedback_trains_procedure_once(durable):
    dt, llm = durable
    started = dt.start_background_task("Summarise my unread email", "")
    wf = started["workflow_id"]
    wait_for(dt, wf, status="SUCCESS")

    assert dt.give_feedback(wf, False) == {"ok": True, "learned": True}
    again = dt.give_feedback(wf, False)
    assert again["learned"] is False and "already rated" in again["note"]
    assert dt.give_feedback("not-a-real-id", True)["ok"] is False


def test_durable_sleep(durable):
    dt, llm = durable
    t0 = time.time()
    started = dt.start_background_task("Remind me to stretch", "", delay_seconds=1.5)
    wait_for(dt, started["workflow_id"], state="scheduled", timeout=5)
    wait_for(dt, started["workflow_id"], status="SUCCESS")
    assert time.time() - t0 >= 1.5


def test_approval_approve(durable):
    dt, llm = durable
    started = dt.start_background_task("Delete old screenshots", "", wait_for_approval=True)
    wf = started["workflow_id"]
    wait_for(dt, wf, state="waiting_for_approval")
    assert task_row(started["task_id"])["status"] == "waiting_approval"

    assert dt.answer_approval(wf, True) == "ok"
    info = wait_for(dt, wf, status="SUCCESS")
    assert info["output"]["cancelled"] is False
    assert dt.answer_approval(wf, True) == "not_waiting"  # already finished


def test_approval_reject(durable):
    dt, llm = durable
    calls_before = len(llm.prompts)
    started = dt.start_background_task("Format the hard drive", "", wait_for_approval=True)
    wf = started["workflow_id"]
    wait_for(dt, wf, state="waiting_for_approval")
    dt.answer_approval(wf, False)
    info = wait_for(dt, wf, status="SUCCESS")
    assert info["output"]["cancelled"] is True and "rejected" in info["output"]["result"]
    assert len(llm.prompts) == calls_before  # the LLM never ran
    assert task_row(started["task_id"])["status"] == "cancelled"


def test_approval_timeout(durable):
    dt, llm = durable
    started = dt.start_background_task(
        "Order pizza", "", wait_for_approval=True, approval_timeout_seconds=1
    )
    info = wait_for(dt, started["workflow_id"], status="SUCCESS")
    assert "timed out" in info["output"]["result"]


def test_llm_error_is_retried(durable):
    dt, llm = durable
    llm.fail_next = 1
    started = dt.start_background_task("Write a haiku about cats", "")
    info = wait_for(dt, started["workflow_id"], status="SUCCESS", timeout=40)
    assert info["output"]["result"] == "Renamed 12 photos."


def test_llm_down_marks_task_failed_and_notifies(durable):
    dt, llm = durable
    llm.fail_next = 3  # every retry fails
    started = dt.start_background_task("Check the weather", "")
    info = wait_for(dt, started["workflow_id"], status="SUCCESS", timeout=60)
    assert info["output"]["failed"] is True and "Groq hiccup" in info["output"]["result"]
    assert info["state"] == "failed"
    row = task_row(started["task_id"])
    assert row["status"] == "failed" and row["result_summary"].startswith("Failed:")
    notes = [n for n in dt.get_notifications() if n["workflow_id"] == started["workflow_id"]]
    assert len(notes) == 1 and "couldn't finish" in notes[0]["message"]


def test_llm_error_text_is_a_failure_not_a_result(durable):
    dt, llm = durable
    llm.reply_override = "Online LLM not configured (Groq client missing)."
    try:
        started = dt.start_background_task("Check the news", "")
        info = wait_for(dt, started["workflow_id"], status="SUCCESS")
    finally:
        llm.reply_override = None
    assert info["output"]["failed"] is True
    assert info["output"]["procedure_id"] is None
    assert task_row(started["task_id"])["status"] == "failed"


def test_empty_llm_reply_is_a_failure(durable):
    dt, llm = durable
    llm.reply_override = "   "
    try:
        started = dt.start_background_task("Check the stocks", "")
        info = wait_for(dt, started["workflow_id"], status="SUCCESS")
    finally:
        llm.reply_override = None
    assert info["output"]["failed"] is True and "empty reply" in info["output"]["result"]


def test_cannot_approve_task_that_never_asked(durable):
    dt, llm = durable
    started = dt.start_background_task("Sleepy task", "", delay_seconds=2)
    wait_for(dt, started["workflow_id"], state="scheduled", timeout=5)
    assert dt.answer_approval(started["workflow_id"], True) == "not_waiting"
    wait_for(dt, started["workflow_id"], status="SUCCESS")


def test_approval_sent_during_delay_is_kept(durable):
    dt, llm = durable
    # Approved straight away, but it still mustn't start before its delay is up
    t0 = time.time()
    started = dt.start_background_task("Later task", "", delay_seconds=2, wait_for_approval=True)
    wait_for(dt, started["workflow_id"], state="waiting_for_approval", timeout=5)
    assert dt.answer_approval(started["workflow_id"], True) == "ok"
    wait_for(dt, started["workflow_id"], state="scheduled", timeout=5)
    info = wait_for(dt, started["workflow_id"], status="SUCCESS")
    assert info["output"]["cancelled"] is False
    assert time.time() - t0 >= 2


def test_bad_task_rating_corrects_procedure(durable):
    from backend import procedure_memory
    dt, llm = durable
    started = dt.start_background_task("Plan a birthday party", "")
    info = wait_for(dt, started["workflow_id"], status="SUCCESS")
    pid = info["output"]["procedure_id"]
    before = next(p for p in procedure_memory.list_procedures() if p["id"] == pid)
    dt.give_feedback(started["workflow_id"], False)
    after = next(p for p in procedure_memory.list_procedures(include_retired=True) if p["id"] == pid)
    assert after["successes"] == before["successes"] - 1
    assert after["failures"] == before["failures"] + 1


def test_llm_error_text_is_retried_before_giving_up(durable):
    dt, llm = durable
    llm.reply_queue = ["[LOCAL LLM ERROR] Read timed out"]  # one hiccup, then fine
    started = dt.start_background_task("Draft a tweet", "")
    info = wait_for(dt, started["workflow_id"], status="SUCCESS", timeout=40)
    assert info["output"]["failed"] is False
    assert info["output"]["result"] == "Renamed 12 photos."


def test_orphaned_task_row_is_failed_on_startup(durable):
    import json as _json
    from backend.tasks_engine import TaskEngine
    dt, llm = durable
    tid = TaskEngine().create_task(
        "Ghost task", "", context_json=_json.dumps({"durable": True, "workflow_id": "sarah-task-ghost"})
    )
    dt._fail_orphaned_tasks()
    row = task_row(tid)
    assert row["status"] == "failed" and "stopped before" in row["result_summary"]
    assert any(n["workflow_id"] == "sarah-task-ghost" for n in dt.get_notifications(limit=500))


def test_list_is_newest_first_across_both_kinds(durable):
    dt, llm = durable
    old = dt.start_background_task("Old request", "", wait_for_approval=True)
    dt.answer_approval(old["workflow_id"], False)
    time.sleep(1.1)  # SQLite timestamps have 1 s resolution
    new = dt.start_background_task("New plain task", "")
    listed = dt.list_tasks(limit=2)
    assert listed[0]["workflow_id"] == new["workflow_id"]
    assert listed[1]["workflow_id"] == old["workflow_id"]


def test_cancelled_workflow_reports_cancelled_state(durable):
    from dbos import DBOS
    dt, llm = durable
    started = dt.start_background_task("Next year", "", delay_seconds=3600)
    DBOS.cancel_workflow(started["workflow_id"])
    assert dt.get_task_status(started["workflow_id"])["state"] == "cancelled"


def test_looks_like_llm_error():
    from backend.durable_tasks import looks_like_llm_error
    assert looks_like_llm_error("[LOCAL LLM ERROR] timeout")
    assert looks_like_llm_error("[LOCAL LLM] No local server configured")
    assert not looks_like_llm_error("STEPS:\n1. ok\nRESULT:\nfine")
    assert not looks_like_llm_error("")


def test_approve_and_reject_race_only_one_wins(durable):
    from concurrent.futures import ThreadPoolExecutor
    dt, llm = durable
    started = dt.start_background_task("Race task", "", wait_for_approval=True)
    wf = started["workflow_id"]
    with ThreadPoolExecutor(2) as pool:
        a, r = pool.map(lambda ok: dt.answer_approval(wf, ok), [True, False])
    assert sorted([a, r]) == ["not_waiting", "ok"]
    info = wait_for(dt, wf, status="SUCCESS")
    assert info["output"]["cancelled"] is (r == "ok")


def test_listing_includes_waiting_tasks(durable):
    dt, llm = durable
    started = dt.start_background_task("Listed task", "", wait_for_approval=True)
    listed = {t["workflow_id"]: t for t in dt.list_tasks(limit=100)}
    assert listed[started["workflow_id"]]["state"] == "waiting_for_approval"
    dt.answer_approval(started["workflow_id"], False)


def test_rerun_step_cannot_duplicate_notification(durable):
    # DBOS steps are at-least-once: simulate _notify running twice after a crash
    dt, llm = durable
    dt._notify(1, "wf-dup-test", "hello")
    dt._notify(1, "wf-dup-test", "hello")
    rows = [n for n in dt.get_notifications(unread_only=False, limit=500) if n["workflow_id"] == "wf-dup-test"]
    assert len(rows) == 1


def test_unknown_workflow(durable):
    dt, llm = durable
    assert dt.get_task_status("nope") is None
    assert dt.answer_approval("nope", True) == "not_found"


# ------------------------------------------------------------
# Crash tests: kill the process mid-task, restart, task resumes
# ------------------------------------------------------------
def _start_worker(data_dir, *args):
    return subprocess.Popen(
        [sys.executable, str(WORKER), str(data_dir), *args],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )


def _first_json_line(proc, timeout=60):
    deadline = time.time() + timeout
    while time.time() < deadline:
        line = proc.stdout.readline()
        if line.startswith("{"):
            return json.loads(line)
        if proc.poll() is not None:
            break
    raise AssertionError(f"worker gave no output. stderr:\n{proc.stderr.read()}")


def _run_resume(data_dir, *args):
    done = subprocess.run(
        [sys.executable, str(WORKER), str(data_dir), "resume", *args],
        capture_output=True, text=True, timeout=120,
    )
    lines = [l for l in done.stdout.splitlines() if l.startswith("{")]
    assert lines, f"resume failed:\n{done.stderr}"
    return json.loads(lines[-1])


def test_crash_during_sleep_resumes_with_remaining_time(tmp_path):
    proc = _start_worker(tmp_path, "start-sleep")
    started = _first_json_line(proc)
    time.sleep(1)
    proc.kill()  # simulated crash / power cut
    proc.wait()
    time.sleep(4)  # "PC is off" for a while; 5 of the 6 s delay have now passed

    resumed_at = time.time()
    finished = _run_resume(tmp_path, started["workflow_id"])
    assert finished["result"]["result"] == "All done."
    # The 6 s delay was honoured (not skipped by the crash)...
    assert finished["finished_at"] - started["started_at"] >= 6
    # ...and not restarted from zero: a reset timer would need 6 more seconds
    assert finished["finished_at"] - resumed_at < 5
    assert (tmp_path / "llm_calls.txt").read_text().count("call") == 1


def test_clean_shutdown_is_quick_and_keeps_waiting_tasks(tmp_path):
    # Regression test: tasks that are waiting must never block shutdown
    t0 = time.time()
    done = subprocess.run(
        [sys.executable, str(WORKER), str(tmp_path), "start-and-shutdown"],
        capture_output=True, text=True, timeout=60,
    )
    assert done.returncode == 0, done.stderr
    assert time.time() - t0 < 20
    ids = json.loads([l for l in done.stdout.splitlines() if l.startswith("{")][-1])

    # After a restart both tasks are still there: approve one and it runs
    finished = _run_resume(tmp_path, ids["approval"], "approve")
    assert finished["result"]["result"] == "All done."
    status = subprocess.run(
        [sys.executable, str(WORKER), str(tmp_path), "status", ids["delayed"]],
        capture_output=True, text=True, timeout=60,
    )
    assert json.loads(status.stdout.strip().splitlines()[-1])["state"] == "scheduled"


def test_crash_while_waiting_for_approval(tmp_path):
    proc = _start_worker(tmp_path, "start-approval")
    started = _first_json_line(proc)
    proc.kill()
    proc.wait()
    assert not (tmp_path / "llm_calls.txt").exists()

    finished = _run_resume(tmp_path, started["workflow_id"], "approve")
    assert finished["result"]["cancelled"] is False
    assert finished["result"]["result"] == "All done."
    assert (tmp_path / "llm_calls.txt").read_text().count("call") == 1
