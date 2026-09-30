# backend/procedure_memory.py
"""
Procedure memory (inspired by MemP, https://github.com/zjunlp/MemP).

Sarah remembers HOW she finished a task, not just what was said:
  - Build:    a finished task's steps are saved as a reusable "procedure"
  - Retrieve: similar future tasks get the best matching procedures
  - Update:   good results strengthen a procedure, bad results weaken it,
              and procedures that keep failing are retired automatically
"""
import json
import threading
from typing import Any, Dict, List, Optional

from backend.db import get_connection
from backend.utils.text_similarity import similarity

MATCH_THRESHOLD = 0.5      # how similar a task must be to reuse a procedure
RETIRE_MIN_ATTEMPTS = 3    # never retire on too little evidence
RETIRE_BELOW_RATE = 0.4    # retire when success rate drops under this

_write_lock = threading.Lock()  # match-then-insert must not race


def init_tables() -> None:
    conn = get_connection()
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS procedures (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            task         TEXT NOT NULL,
            steps_json   TEXT NOT NULL,
            successes    INTEGER NOT NULL DEFAULT 0,
            failures     INTEGER NOT NULL DEFAULT 0,
            retired      INTEGER NOT NULL DEFAULT 0,
            created_at   TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at   TEXT DEFAULT CURRENT_TIMESTAMP
        );
        """
    )
    conn.commit()
    conn.close()


def _success_rate(successes: int, failures: int) -> float:
    # Smoothed so a brand-new procedure starts at 0.5-ish instead of 0 or 1
    return (successes + 1) / (successes + failures + 2)


def _should_retire(successes: int, failures: int) -> bool:
    return (
        successes + failures >= RETIRE_MIN_ATTEMPTS
        and _success_rate(successes, failures) < RETIRE_BELOW_RATE
    )


def _row_to_dict(row) -> Dict[str, Any]:
    return {
        "id": row["id"],
        "task": row["task"],
        "steps": json.loads(row["steps_json"]),
        "successes": row["successes"],
        "failures": row["failures"],
        "retired": bool(row["retired"]),
        "success_rate": round(_success_rate(row["successes"], row["failures"]), 3),
        "updated_at": row["updated_at"],
    }


def _best_match(conn, task: str) -> Optional[Any]:
    rows = conn.execute("SELECT * FROM procedures WHERE retired = 0").fetchall()
    best, best_score = None, 0.0
    for row in rows:
        score = similarity(task, row["task"])
        if score > best_score:
            best, best_score = row, score
    return best if best_score >= MATCH_THRESHOLD else None


def record_attempt(task: str, steps: List[str], success: bool) -> Optional[int]:
    """
    Learn from one attempt at a task. Returns the procedure id it touched,
    or None when a failed attempt had nothing to update.
    """
    steps = [s.strip() for s in steps if s and s.strip()]
    with _write_lock:
        return _record_attempt_locked(task, steps, success)


def _record_attempt_locked(task: str, steps: List[str], success: bool) -> Optional[int]:
    conn = get_connection()
    try:
        match = _best_match(conn, task)

        if match is None:
            if not success or not steps:
                return None  # only successful attempts become new procedures
            cur = conn.execute(
                "INSERT INTO procedures (task, steps_json, successes) VALUES (?, ?, 1)",
                (task, json.dumps(steps)),
            )
            conn.commit()
            return cur.lastrowid

        successes, failures = match["successes"], match["failures"]
        steps_json = match["steps_json"]
        if success:
            # A procedure that was mostly failing gets replaced by what just worked
            if steps and failures >= successes:
                steps_json = json.dumps(steps)
            successes += 1
        else:
            failures += 1

        retired = _should_retire(successes, failures)
        conn.execute(
            """
            UPDATE procedures
            SET steps_json = ?, successes = ?, failures = ?, retired = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (steps_json, successes, failures, int(retired), match["id"]),
        )
        conn.commit()
        return match["id"]
    finally:
        conn.close()


def record_feedback(procedure_id: int, success: bool, correct_provisional: bool = False) -> bool:
    """
    Creator said a result was good/bad. Returns False if the id is unknown.

    correct_provisional=True is for a run that record_attempt() already
    counted as a success: "good" confirms it (no change), "bad" turns that
    success into a failure instead of just adding one next to it.
    """
    with _write_lock:
        return _record_feedback_locked(procedure_id, success, correct_provisional)


def _record_feedback_locked(procedure_id: int, success: bool, correct_provisional: bool) -> bool:
    conn = get_connection()
    try:
        row = conn.execute("SELECT * FROM procedures WHERE id = ?", (procedure_id,)).fetchone()
        if row is None:
            return False
        successes, failures = row["successes"], row["failures"]
        if correct_provisional:
            if not success:
                successes = max(0, successes - 1)
                failures += 1
        elif success:
            successes += 1
        else:
            failures += 1
        retired = _should_retire(successes, failures)
        conn.execute(
            """
            UPDATE procedures
            SET successes = ?, failures = ?, retired = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (successes, failures, int(retired), procedure_id),
        )
        conn.commit()
        return True
    finally:
        conn.close()


def find_procedures(task: str, limit: int = 3) -> List[Dict[str, Any]]:
    """Best reusable procedures for a task, ranked by similarity x reliability."""
    conn = get_connection()
    try:
        rows = conn.execute("SELECT * FROM procedures WHERE retired = 0").fetchall()
    finally:
        conn.close()

    scored = []
    for row in rows:
        sim = similarity(task, row["task"])
        if sim >= MATCH_THRESHOLD:
            scored.append((sim * _success_rate(row["successes"], row["failures"]), row))
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [_row_to_dict(row) for _, row in scored[:limit]]


def list_procedures(include_retired: bool = False, limit: int = 100) -> List[Dict[str, Any]]:
    conn = get_connection()
    try:
        if include_retired:
            rows = conn.execute(
                "SELECT * FROM procedures ORDER BY updated_at DESC LIMIT ?", (limit,)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM procedures WHERE retired = 0 ORDER BY updated_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
    finally:
        conn.close()
    return [_row_to_dict(r) for r in rows]


def format_for_prompt(task: str, limit: int = 2) -> str:
    """Text block to drop into an LLM prompt, or '' if nothing relevant."""
    procs = find_procedures(task, limit=limit)
    if not procs:
        return ""
    lines = ["Procedures that worked for similar tasks before:"]
    for p in procs:
        lines.append(f'- For "{p["task"]}" (success rate {p["success_rate"]:.0%}):')
        for i, step in enumerate(p["steps"], 1):
            lines.append(f"    {i}. {step}")
    return "\n".join(lines)
