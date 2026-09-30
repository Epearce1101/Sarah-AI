# backend/proactive_engine.py
"""
Proactive suggestion engine (inspired by THUNLP ProactiveAgent,
https://github.com/thunlp/ProactiveAgent).

Decides WHEN Sarah should offer help on her own, and learns from how the
Creator reacts: accepted suggestions make that kind of offer more likely,
rejected or ignored ones make Sarah back off.
"""
import threading
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional, Tuple

from backend.db import get_connection
from backend.utils.text_similarity import similarity

GLOBAL_COOLDOWN_MIN = 10      # minimum gap between any two offers
IGNORE_AFTER_MIN = 15         # unanswered offers count as "ignored" after this
DUPLICATE_WINDOW_MIN = 60     # don't repeat a near-identical offer within this
DUPLICATE_SIMILARITY = 0.8
HISTORY_WINDOW = 30           # only the latest N outcomes per category matter
HISTORY_DAYS = 7              # ...from the last N days, so a muted category recovers
MIN_SAMPLES = 4               # need this many outcomes before backing off
MIN_ACCEPT_SCORE = 0.3        # below this, stop offering in that category

VALID_OUTCOMES = ("accepted", "rejected", "ignored")
_offer_lock = threading.Lock()  # two requests at once must not both get "yes"
_TS_FORMAT = "%Y-%m-%d %H:%M:%S"


def _now() -> datetime:
    # SQLite CURRENT_TIMESTAMP is UTC, so compare in UTC too
    return datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)


def _parse(ts: str) -> datetime:
    return datetime.strptime(ts, _TS_FORMAT)


def init_tables() -> None:
    conn = get_connection()
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS proactive_suggestions (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            category     TEXT NOT NULL,
            text         TEXT NOT NULL,
            context      TEXT,
            status       TEXT NOT NULL DEFAULT 'pending',
            created_at   TEXT NOT NULL,
            resolved_at  TEXT
        );
        """
    )
    conn.commit()
    conn.close()


def expire_stale(now: Optional[datetime] = None) -> int:
    """Mark old unanswered suggestions as ignored. Returns how many changed."""
    now = now or _now()
    cutoff = (now - timedelta(minutes=IGNORE_AFTER_MIN)).strftime(_TS_FORMAT)
    conn = get_connection()
    try:
        cur = conn.execute(
            """
            UPDATE proactive_suggestions
            SET status = 'ignored', resolved_at = ?
            WHERE status = 'pending' AND created_at <= ?
            """,
            (now.strftime(_TS_FORMAT), cutoff),
        )
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


def acceptance_score(category: str, now: Optional[datetime] = None) -> Tuple[float, int]:
    """
    Smoothed acceptance rate for a category over recent outcomes.
    Accepted = 1, ignored = 0.25 (maybe they were busy), rejected = 0.
    Returns (score, number_of_outcomes).
    """
    now = now or _now()
    since = (now - timedelta(days=HISTORY_DAYS)).strftime(_TS_FORMAT)
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT status FROM proactive_suggestions
            WHERE category = ? AND status != 'pending' AND created_at >= ?
            ORDER BY id DESC LIMIT ?
            """,
            (category, since, HISTORY_WINDOW),
        ).fetchall()
    finally:
        conn.close()

    weights = {"accepted": 1.0, "ignored": 0.25, "rejected": 0.0}
    total = sum(weights.get(r["status"], 0.0) for r in rows)
    n = len(rows)
    return (total + 1) / (n + 2), n


def should_offer(category: str, text: str, now: Optional[datetime] = None) -> Tuple[bool, str]:
    """Should Sarah speak up with this suggestion right now? Returns (yes/no, reason)."""
    now = now or _now()
    expire_stale(now)

    conn = get_connection()
    try:
        pending = conn.execute(
            "SELECT COUNT(*) AS c FROM proactive_suggestions WHERE status = 'pending'"
        ).fetchone()["c"]
        last = conn.execute(
            "SELECT created_at FROM proactive_suggestions ORDER BY id DESC LIMIT 1"
        ).fetchone()
        dup_cutoff = (now - timedelta(minutes=DUPLICATE_WINDOW_MIN)).strftime(_TS_FORMAT)
        recent_texts = conn.execute(
            "SELECT text FROM proactive_suggestions WHERE created_at >= ?",
            (dup_cutoff,),
        ).fetchall()
    finally:
        conn.close()

    if pending:
        return False, "waiting on an earlier suggestion"
    if last and now - _parse(last["created_at"]) < timedelta(minutes=GLOBAL_COOLDOWN_MIN):
        return False, "cooldown"
    if any(similarity(text, r["text"]) >= DUPLICATE_SIMILARITY for r in recent_texts):
        return False, "already suggested recently"

    score, n = acceptance_score(category, now)
    if n >= MIN_SAMPLES and score < MIN_ACCEPT_SCORE:
        return False, f"creator usually declines '{category}' suggestions"
    return True, "ok"


def record_suggestion(category: str, text: str, context: Optional[str] = None,
                      now: Optional[datetime] = None) -> int:
    now = now or _now()
    conn = get_connection()
    try:
        cur = conn.execute(
            """
            INSERT INTO proactive_suggestions (category, text, context, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (category, text, context, now.strftime(_TS_FORMAT)),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def maybe_offer(category: str, text: str, context: Optional[str] = None,
                now: Optional[datetime] = None) -> Dict[str, Any]:
    """Check + record in one go. Returns {offer, reason, suggestion_id}."""
    with _offer_lock:
        ok, reason = should_offer(category, text, now)
        suggestion_id = record_suggestion(category, text, context, now) if ok else None
    return {"offer": ok, "reason": reason, "suggestion_id": suggestion_id}


def record_feedback(suggestion_id: int, outcome: str, now: Optional[datetime] = None) -> bool:
    """outcome: accepted | rejected | ignored. Returns False for unknown ids."""
    if outcome not in VALID_OUTCOMES:
        raise ValueError(f"outcome must be one of {VALID_OUTCOMES}")
    now = now or _now()
    conn = get_connection()
    try:
        cur = conn.execute(
            "UPDATE proactive_suggestions SET status = ?, resolved_at = ? WHERE id = ?",
            (outcome, now.strftime(_TS_FORMAT), suggestion_id),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def stats() -> Dict[str, Any]:
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT category, status, COUNT(*) AS c
            FROM proactive_suggestions GROUP BY category, status
            """
        ).fetchall()
    finally:
        conn.close()

    out: Dict[str, Any] = {}
    for r in rows:
        out.setdefault(r["category"], {})[r["status"]] = r["c"]
    for category in out:
        score, n = acceptance_score(category)
        out[category]["acceptance_score"] = round(score, 3)
        out[category]["outcomes"] = n
    return out
