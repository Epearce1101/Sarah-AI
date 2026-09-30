# backend/screen_timeline.py
"""
Screen timeline — a lightweight, free take on StreamForest's idea of
"recent details + longer-term event memory".

Every screenshot description Sarah already gets from the vision model is
saved as an event. Near-identical back-to-back descriptions are merged into
one event (so a paused game doesn't flood the log), and the timeline can be
summarised by the LLM into "what's happened so far".
"""
import threading
from typing import Any, Awaitable, Callable, Dict, List, Optional

from backend.db import get_connection
from backend.utils.text_similarity import similarity

MERGE_SIMILARITY = 0.75   # consecutive descriptions this similar = same event
MAX_EVENTS_KEPT = 2000    # oldest events are pruned past this

_write_lock = threading.Lock()  # compare-with-last-then-insert must not race


def init_tables() -> None:
    conn = get_connection()
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS screen_events (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            description   TEXT NOT NULL,
            suggestions   TEXT,
            repeat_count  INTEGER NOT NULL DEFAULT 1,
            first_seen    TEXT DEFAULT CURRENT_TIMESTAMP,
            last_seen     TEXT DEFAULT CURRENT_TIMESTAMP
        );
        """
    )
    conn.commit()
    conn.close()


def add_event(description: str, suggestions: Optional[str] = None) -> Optional[int]:
    """Save one screen description. Returns the event id (new or merged)."""
    description = (description or "").strip()
    if not description:
        return None

    with _write_lock:
        return _add_event_locked(description, suggestions)


def _add_event_locked(description: str, suggestions: Optional[str]) -> Optional[int]:
    conn = get_connection()
    try:
        last = conn.execute(
            "SELECT id, description FROM screen_events ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if last and similarity(description, last["description"]) >= MERGE_SIMILARITY:
            conn.execute(
                """
                UPDATE screen_events
                SET repeat_count = repeat_count + 1, last_seen = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (last["id"],),
            )
            conn.commit()
            return last["id"]

        cur = conn.execute(
            "INSERT INTO screen_events (description, suggestions) VALUES (?, ?)",
            (description, suggestions),
        )
        event_id = cur.lastrowid
        conn.execute(
            """
            DELETE FROM screen_events WHERE id IN (
                SELECT id FROM screen_events ORDER BY id DESC LIMIT -1 OFFSET ?
            )
            """,
            (MAX_EVENTS_KEPT,),
        )
        conn.commit()
        return event_id
    finally:
        conn.close()


def recent_events(limit: int = 30) -> List[Dict[str, Any]]:
    """Newest-last list of recent events."""
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM screen_events ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in reversed(rows)]


def clear() -> None:
    conn = get_connection()
    conn.execute("DELETE FROM screen_events")
    conn.commit()
    conn.close()


def build_timeline_text(limit: int = 30) -> str:
    lines = []
    for e in recent_events(limit):
        repeat = f" (seen {e['repeat_count']}x)" if e["repeat_count"] > 1 else ""
        lines.append(f"[{e['first_seen']}] {e['description']}{repeat}")
    return "\n".join(lines)


async def summarize(llm: Callable[[str], Awaitable[str]], timeline: str) -> str:
    """Ask the LLM what has happened on screen so far (timeline from build_timeline_text)."""
    if not timeline:
        return "I haven't seen anything on screen yet."
    prompt = (
        "Here is a timeline of what was on the Creator's screen (UTC times, "
        "oldest first):\n\n"
        f"{timeline}\n\n"
        "Summarise what has happened so far in a few short sentences, "
        "focusing on progress and notable moments."
    )
    return await llm(prompt)
