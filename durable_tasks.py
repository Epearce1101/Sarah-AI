# backend/durable_tasks.py
"""
Durable background tasks powered by DBOS (https://github.com/dbos-inc/dbos-transact-py).

"Handle this and get back to me": Sarah works on a task in the background,
and every finished step is checkpointed to SQLite. If the backend crashes or
the PC restarts, the task resumes where it left off when Sarah starts again.
Tasks can be scheduled for later, or wait until the Creator approves them.

When a task finishes, Sarah leaves a notification the UI can poll.

Install:  pip install dbos

How waiting works (important):
  Nothing ever "sleeps" inside a running task. A delayed task is put on a
  DBOS queue with a start time saved in the database, and a task that needs
  approval is only a saved request until someone approves it (a scheduled
  "expiry" job cancels it if nobody answers in time). That keeps shutdown
  instant and loses nothing on a crash or restart.

NOTE: DBOS's regular (non-async) functions refuse to run inside an asyncio
event loop, so FastAPI endpoints that call into this module must be plain
`def` endpoints (FastAPI runs those in a worker thread).
"""
import asyncio
import json
import logging
import re
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from dbos import DBOS, DBOSConfig, SetEnqueueOptions, SetWorkflowID

from backend.db import get_connection
from backend.tasks_engine import TaskEngine
from backend import procedure_memory

WORKFLOW_NAME = "sarah_background_task"
QUEUE_NAME = "sarah_tasks"
QUEUE_CONCURRENCY = 2      # tasks running at once (keeps Groq's free limits happy)
STATE_EVENT = "state"
# Fixed on purpose: DBOS only resumes tasks saved under the same version, and
# by default that version changes whenever this file is edited. Bump it only
# if the workflow's steps change in a way old saved tasks can't replay.
APP_VERSION = "sarah-v11-tasks-1"

# Task statuses this module writes to the tasks table:
#   pending -> in_progress -> done | failed
#   waiting_approval -> (approved) pending ... | cancelled (rejected / timed out)

log = logging.getLogger("sarah.durable")

# Set by configure(); a plain (blocking) function prompt -> reply text.
_llm: Optional[Callable[[str], str]] = None
_dbos_instance: Optional[DBOS] = None
_launched = False


# ------------------------------------------------------------
# SETUP
# ------------------------------------------------------------
def init_tables() -> None:
    conn = get_connection()
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS sarah_notifications (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id      INTEGER,
            workflow_id  TEXT,
            message      TEXT NOT NULL,
            is_read      INTEGER NOT NULL DEFAULT 0,
            created_at   TEXT DEFAULT CURRENT_TIMESTAMP
        );
        """
    )
    # One notification per task: a step re-run after a crash can't duplicate it
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_notifications_workflow "
        "ON sarah_notifications(workflow_id)"
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS durable_task_feedback (
            workflow_id  TEXT PRIMARY KEY,
            good         INTEGER NOT NULL,
            created_at   TEXT DEFAULT CURRENT_TIMESTAMP
        );
        """
    )
    # Tasks waiting for the Creator's OK. state: waiting | approved | rejected | expired
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS durable_task_requests (
            workflow_id  TEXT PRIMARY KEY,
            task_id      INTEGER NOT NULL,
            title        TEXT NOT NULL,
            instructions TEXT,
            run_at       REAL NOT NULL,
            expires_at   REAL NOT NULL,
            state        TEXT NOT NULL DEFAULT 'waiting',
            result       TEXT,
            created_at   TEXT DEFAULT CURRENT_TIMESTAMP
        );
        """
    )
    conn.commit()
    conn.close()


def llm_from_async(async_llm: Callable[..., Any]) -> Callable[[str], str]:
    """Wrap an async `llm(prompt)` (like SarahCore._call_llm) for DBOS worker threads."""
    def _call(prompt: str) -> str:
        return asyncio.run(async_llm(prompt))
    return _call


def configure(llm: Callable[[str], str], db_path: Path) -> None:
    """Must be called once, before launch()."""
    global _llm, _dbos_instance
    _llm = llm
    if _dbos_instance is None:
        config: DBOSConfig = {
            "name": "sarah-ai",
            "system_database_url": f"sqlite:///{Path(db_path).as_posix()}",
            "log_level": "ERROR",  # retries are expected; only show real errors
            "application_version": APP_VERSION,
        }
        _dbos_instance = DBOS(config=config)


def launch() -> None:
    """Start DBOS. Interrupted and scheduled tasks carry on from here."""
    global _launched
    if _dbos_instance is None:
        raise RuntimeError("durable_tasks.configure() must be called first")
    if not _launched:
        DBOS.launch()
        DBOS.register_queue(QUEUE_NAME, worker_concurrency=QUEUE_CONCURRENCY)
        _launched = True
        # Housekeeping for rare crash windows; must not take the feature down
        for fix in (_reschedule_waiting_expiries, _fail_orphaned_tasks):
            try:
                fix()
            except Exception as e:
                log.warning(f"[DURABLE] {fix.__name__} failed: {e}")


def shutdown() -> None:
    global _launched, _dbos_instance
    if _dbos_instance is not None:
        DBOS.destroy()
    _launched = False
    _dbos_instance = None


def is_running() -> bool:
    return _launched


def _enqueue(workflow_id: str, delay_seconds: float, func: Callable, *args: Any) -> None:
    """Queue a workflow (optionally for later). Same id twice = no-op."""
    queue = DBOS.retrieve_queue(QUEUE_NAME)
    delay = delay_seconds if delay_seconds and delay_seconds > 0 else None
    with SetWorkflowID(workflow_id), SetEnqueueOptions(delay_seconds=delay):
        queue.enqueue(func, *args)


# ------------------------------------------------------------
# STEPS (each one is checkpointed; finished steps never re-run)
# ------------------------------------------------------------
@DBOS.step()
def _now() -> float:
    return time.time()


@DBOS.step()
def _set_task_status(task_id: int, status: str) -> None:
    TaskEngine().update_status(task_id, status)


@DBOS.step()
def _lookup_procedures(task_text: str) -> str:
    return procedure_memory.format_for_prompt(task_text)


class LLMReplyError(RuntimeError):
    pass


@DBOS.step(retries_allowed=True, max_attempts=3, interval_seconds=3)
def _run_llm(prompt: str) -> str:
    if _llm is None:
        raise RuntimeError("No LLM configured for durable tasks")
    reply = _llm(prompt)
    # Error replies and blank replies raise, so DBOS retries them like any failure
    if not reply or not reply.strip():
        raise LLMReplyError("The LLM returned an empty reply.")
    if looks_like_llm_error(reply):
        raise LLMReplyError(reply.strip())
    return reply


@DBOS.step()
def _learn_procedure(task_text: str, steps: List[str]) -> Optional[int]:
    if not steps:
        return None
    return procedure_memory.record_attempt(task_text, steps, success=True)


def _notify(task_id: int, workflow_id: str, message: str) -> None:
    conn = get_connection()
    try:
        conn.execute(
            "INSERT OR IGNORE INTO sarah_notifications (task_id, workflow_id, message) "
            "VALUES (?, ?, ?)",
            (task_id, workflow_id, message),
        )
        conn.commit()
    finally:
        conn.close()


@DBOS.step()
def _finish(task_id: int, workflow_id: str, summary: str, message: str,
            status: str = "done") -> None:
    TaskEngine().set_result(task_id, summary, status=status)
    _notify(task_id, workflow_id, message)


@DBOS.step()
def _claim_request(workflow_id: str, new_state: str, result: Optional[str]) -> Optional[Dict[str, Any]]:
    """
    Move a request out of 'waiting'. Only the first of approve / reject /
    expire can win; everyone else gets None.
    """
    conn = get_connection()
    try:
        cur = conn.execute(
            "UPDATE durable_task_requests SET state = ?, result = ? "
            "WHERE workflow_id = ? AND state = 'waiting'",
            (new_state, result, workflow_id),
        )
        conn.commit()
        if cur.rowcount == 0:
            return None
        row = conn.execute(
            "SELECT * FROM durable_task_requests WHERE workflow_id = ?", (workflow_id,)
        ).fetchone()
        return dict(row)
    finally:
        conn.close()


# ------------------------------------------------------------
# PROMPT + PARSING
# ------------------------------------------------------------
def build_prompt(title: str, instructions: str, procedures_text: str) -> str:
    parts = [
        "You are working on a background task for your Creator.",
        f"Task: {title}",
    ]
    if instructions and instructions.strip() != title.strip():
        parts.append(f"Details: {instructions}")
    if procedures_text:
        parts.append(procedures_text)
    parts.append(
        "Reply in exactly this format:\n"
        "STEPS:\n1. <first step you took>\n2. <next step>\n...\n"
        "RESULT:\n<the finished result for the Creator>"
    )
    return "\n\n".join(parts)


# SarahCore's LLM client returns these instead of raising when it can't answer
_LLM_ERROR_PREFIXES = (
    "online llm not configured",
    "[local llm",
)


def looks_like_llm_error(reply: str) -> bool:
    return (reply or "").strip().lower().startswith(_LLM_ERROR_PREFIXES)


_STEP_LINE = re.compile(r"^\s*(?:\d+[.)]|[-*])\s+(.*\S)\s*$")


def parse_reply(reply: str) -> Tuple[List[str], str]:
    """Split an LLM reply into (steps, result). Falls back to the whole reply."""
    reply = reply or ""
    match = re.search(r"STEPS:\s*(.*?)\s*RESULT:\s*(.*)", reply, re.S | re.I)
    if not match:
        return [], reply.strip()
    steps = []
    for line in match.group(1).splitlines():
        m = _STEP_LINE.match(line)
        if m:
            steps.append(m.group(1))
    result = match.group(2).strip() or reply.strip()
    return steps, result


def _cancelled_output(task_id: int, summary: str) -> Dict[str, Any]:
    return {"task_id": task_id, "cancelled": True, "failed": False,
            "result": summary, "steps": [], "procedure_id": None}


# ------------------------------------------------------------
# WORKFLOWS
# ------------------------------------------------------------
@DBOS.workflow(name=WORKFLOW_NAME)
def background_task_workflow(task_id: int, title: str, instructions: str) -> Dict[str, Any]:
    """Do the task. Runs once it's due (and approved, if approval was needed)."""
    workflow_id = DBOS.workflow_id
    _set_task_status(task_id, "in_progress")
    DBOS.set_event(STATE_EVENT, "working")

    # Procedures are matched on the title (the "what"); details vary per run
    procedures_text = _lookup_procedures(title)
    error = None
    try:
        reply = _run_llm(build_prompt(title, instructions, procedures_text))
    # (DBOS workflow cancellation is a BaseException, so it isn't swallowed here)
    except Exception as e:  # still failing after the step's retries
        # DBOS wraps the real problem; show the Creator the last actual error
        last = (getattr(e, "errors", None) or [e])[-1]
        error = str(last) or type(last).__name__
    if error:
        _finish(task_id, workflow_id, f"Failed: {error}",
                f'I couldn\'t finish "{title}": {error[:300]}', "failed")
        DBOS.set_event(STATE_EVENT, "failed")
        return {"task_id": task_id, "cancelled": False, "failed": True,
                "result": f"Failed: {error}", "steps": [], "procedure_id": None}

    steps, result = parse_reply(reply)
    procedure_id = _learn_procedure(title, steps)

    _finish(task_id, workflow_id, result, f'I finished "{title}". {result[:300]}')
    DBOS.set_event(STATE_EVENT, "done")
    return {"task_id": task_id, "cancelled": False, "failed": False,
            "result": result, "steps": steps, "procedure_id": procedure_id}


@DBOS.workflow(name="sarah_task_decision")
def _decision_workflow(workflow_id: str, approve: bool) -> bool:
    """Creator approved/rejected a waiting task. Returns False if too late."""
    if approve:
        req = _claim_request(workflow_id, "approved", None)
        if req is None:
            return False
        delay = max(0.0, req["run_at"] - _now())
        _enqueue(workflow_id, delay, background_task_workflow,
                 req["task_id"], req["title"], req["instructions"] or "")
        return True

    summary = "Cancelled: task was rejected."
    req = _claim_request(workflow_id, "rejected", summary)
    if req is None:
        return False
    _finish(req["task_id"], workflow_id, summary,
            f'I didn\'t do "{req["title"]}" because it was rejected.', "cancelled")
    return True


@DBOS.workflow(name="sarah_task_expiry")
def _expiry_workflow(workflow_id: str) -> bool:
    """Runs when the approval window ends; cancels the task if still waiting."""
    summary = "Cancelled: task timed out waiting for approval."
    req = _claim_request(workflow_id, "expired", summary)
    if req is None:
        return False  # already approved or rejected
    _finish(req["task_id"], workflow_id, summary,
            f'I didn\'t do "{req["title"]}" because it timed out waiting for approval.',
            "cancelled")
    return True


def _reschedule_waiting_expiries() -> None:
    """
    If Sarah crashed right after saving an approval request but before its
    expiry job was queued, queue it now (same id, so existing ones are kept).
    """
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT workflow_id, expires_at FROM durable_task_requests WHERE state = 'waiting'"
        ).fetchall()
    finally:
        conn.close()
    now = time.time()
    for row in rows:
        _enqueue(f"{row['workflow_id']}-expiry", max(0.0, row["expires_at"] - now),
                 _expiry_workflow, row["workflow_id"])


def _fail_orphaned_tasks() -> None:
    """
    A task row whose workflow never got queued (Sarah stopped in between)
    would sit as 'pending' forever; mark it failed and tell the Creator.
    """
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT id, title, context_json FROM tasks WHERE status = 'pending' "
            "AND context_json LIKE '%\"durable\": true%'"
        ).fetchall()
    finally:
        conn.close()
    for row in rows:
        try:
            workflow_id = json.loads(row["context_json"]).get("workflow_id")
        except (TypeError, ValueError):
            continue
        if not workflow_id or DBOS.get_workflow_status(workflow_id) or _get_request(workflow_id):
            continue
        TaskEngine().set_result(row["id"], "Failed: Sarah stopped before this task could start.",
                                status="failed")
        _notify(row["id"], workflow_id,
                f'I couldn\'t start "{row["title"]}" because I was shut down. Please ask again.')


# ------------------------------------------------------------
# PUBLIC API (call from worker threads, not the event loop)
# ------------------------------------------------------------
def start_background_task(
    title: str,
    instructions: str = "",
    delay_seconds: float = 0,
    wait_for_approval: bool = False,
    approval_timeout_seconds: float = 86400,
) -> Dict[str, Any]:
    """
    delay_seconds: don't start before this many seconds from now.
    wait_for_approval: don't start until the Creator approves; cancelled if
    nobody answers within approval_timeout_seconds.
    """
    if not _launched:
        raise RuntimeError("Durable tasks are not running")
    # Random id (not the task number): task ids can repeat if Sarah's main DB
    # is ever reset, and DBOS treats a reused workflow id as "the same task".
    workflow_id = f"sarah-task-{uuid.uuid4().hex[:12]}"
    task_id = TaskEngine().create_task(
        title, instructions,
        context_json=json.dumps({"durable": True, "workflow_id": workflow_id}),
    )

    if not wait_for_approval:
        try:
            _enqueue(workflow_id, delay_seconds, background_task_workflow,
                     task_id, title, instructions)
        except Exception as e:
            TaskEngine().set_result(task_id, f"Failed to start: {e}", status="failed")
            raise
        return {"workflow_id": workflow_id, "task_id": task_id}

    now = time.time()
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT INTO durable_task_requests
                (workflow_id, task_id, title, instructions, run_at, expires_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (workflow_id, task_id, title, instructions,
             now + max(0.0, delay_seconds), now + approval_timeout_seconds),
        )
        conn.commit()
    finally:
        conn.close()
    TaskEngine().update_status(task_id, "waiting_approval")
    _enqueue(f"{workflow_id}-expiry", approval_timeout_seconds, _expiry_workflow, workflow_id)
    return {"workflow_id": workflow_id, "task_id": task_id}


def _get_request(workflow_id: str) -> Optional[Dict[str, Any]]:
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM durable_task_requests WHERE workflow_id = ?", (workflow_id,)
        ).fetchone()
    finally:
        conn.close()
    return dict(row) if row else None


def _request_status(req: Dict[str, Any]) -> Dict[str, Any]:
    info = {"workflow_id": req["workflow_id"], "output": None, "error": None}
    if req["state"] == "waiting":
        info.update(status="PENDING", state="waiting_for_approval")
    elif req["state"] == "approved":  # approved, the task itself is being queued
        info.update(status="ENQUEUED", state="scheduled")
    else:  # rejected / expired
        info.update(status="SUCCESS", state="cancelled",
                    output=_cancelled_output(req["task_id"], req["result"] or "Cancelled."))
    return info


def get_task_status(workflow_id: str) -> Optional[Dict[str, Any]]:
    """
    status: PENDING / ENQUEUED / DELAYED / SUCCESS / ERROR (DBOS's names)
    state:  waiting_for_approval, scheduled, working, done, failed, cancelled
    """
    status = DBOS.get_workflow_status(workflow_id)
    if status is not None and status.name == WORKFLOW_NAME:
        if status.status == "CANCELLED":
            state = "cancelled"
        elif status.status in ("ERROR", "MAX_RECOVERY_ATTEMPTS_EXCEEDED"):
            state = "failed"
        else:
            state = DBOS.get_event(workflow_id, STATE_EVENT, timeout_seconds=0)
            if state is None and status.status in ("ENQUEUED", "DELAYED"):
                state = "scheduled"
        return {
            "workflow_id": workflow_id,
            "status": status.status,
            "state": state,
            "output": status.output if status.status == "SUCCESS" else None,
            "error": str(status.error) if status.error else None,
        }
    req = _get_request(workflow_id)
    return _request_status(req) if req else None


def _sqlite_utc_to_epoch(ts: str) -> float:
    try:
        return datetime.strptime(ts, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc).timestamp()
    except (TypeError, ValueError):
        return 0.0


def list_tasks(limit: int = 20) -> List[Dict[str, Any]]:
    """Newest first, mixing tasks still waiting for approval with started ones."""
    conn = get_connection()
    try:
        # 'approved' requests are listed via their started workflow instead
        reqs = conn.execute(
            "SELECT * FROM durable_task_requests WHERE state != 'approved' "
            "ORDER BY created_at DESC, rowid DESC LIMIT ?",
            (limit,),
        ).fetchall()
    finally:
        conn.close()
    rows = []
    for r in reqs:
        info = _request_status(dict(r))
        rows.append((_sqlite_utc_to_epoch(r["created_at"]), {
            "workflow_id": r["workflow_id"], "status": info["status"],
            "state": info["state"], "task_id": r["task_id"], "output": info["output"],
        }))
    for w in DBOS.list_workflows(name=WORKFLOW_NAME, limit=limit, sort_desc=True):
        args = (w.input or {}).get("args", []) if isinstance(w.input, dict) else []
        rows.append(((w.created_at or 0) / 1000.0, {
            "workflow_id": w.workflow_id,
            "status": w.status,
            "state": None,
            "task_id": args[0] if args else None,
            "output": w.output if w.status == "SUCCESS" else None,
        }))
    rows.sort(key=lambda pair: pair[0], reverse=True)
    return [item for _, item in rows[:limit]]


def answer_approval(workflow_id: str, approve: bool) -> str:
    """
    Returns "ok", "not_found" (not one of Sarah's tasks) or "not_waiting"
    (already approved/rejected/timed out, or it never asked for approval).
    """
    req = _get_request(workflow_id)
    if req is None:
        return "not_waiting" if get_task_status(workflow_id) else "not_found"
    if req["state"] != "waiting":
        return "not_waiting"
    # Separate ids per answer: if approve and reject race, exactly one claims it
    decision_id = f"{workflow_id}-{'approve' if approve else 'reject'}"
    with SetWorkflowID(decision_id):
        handle = DBOS.start_workflow(_decision_workflow, workflow_id, approve)
    return "ok" if handle.get_result() else "not_waiting"


def give_feedback(workflow_id: str, good: bool) -> Dict[str, Any]:
    """Creator rates a finished task; this trains procedure memory."""
    info = get_task_status(workflow_id)
    if info is None:
        return {"ok": False, "error": "Unknown task"}
    output = info.get("output") or {}
    procedure_id = output.get("procedure_id")
    if not procedure_id:
        return {"ok": True, "learned": False}

    conn = get_connection()
    try:
        cur = conn.execute(
            "INSERT OR IGNORE INTO durable_task_feedback (workflow_id, good) VALUES (?, ?)",
            (workflow_id, int(good)),
        )
        conn.commit()
        first_rating = cur.rowcount > 0
    finally:
        conn.close()
    if not first_rating:
        return {"ok": True, "learned": False, "note": "Task was already rated"}
    # The finished run already counted as a success; a bad rating turns it into a failure
    learned = procedure_memory.record_feedback(procedure_id, good, correct_provisional=True)
    return {"ok": True, "learned": learned}


def get_notifications(unread_only: bool = True, limit: int = 50) -> List[Dict[str, Any]]:
    conn = get_connection()
    try:
        sql = "SELECT * FROM sarah_notifications"
        if unread_only:
            sql += " WHERE is_read = 0"
        sql += " ORDER BY id DESC LIMIT ?"
        rows = conn.execute(sql, (limit,)).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


def mark_notification_read(notification_id: int) -> bool:
    conn = get_connection()
    try:
        cur = conn.execute(
            "UPDATE sarah_notifications SET is_read = 1 WHERE id = ?", (notification_id,)
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()
