"""Sarah's memory of her days.

Through the day she keeps a light log of *experiences*: what she saw Zero
doing (app/game changes, not every frame), things she noticed, how her
feelings shifted, what she did with her tools, being touched, Zero coming
back. After a day ends she writes a short private journal entry about it in
her own voice (one free-model request), and pulls out lasting facts into
long-term memory (tag "journal"), so tomorrow she can ask "did you beat
Margit yet?".

Every prompt carries her last couple of journal entries and a few lines of
"earlier today", so her days connect.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import threading
import time
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger("sarah.journal")

_lock = threading.Lock()
_last_text: Dict[str, tuple] = {}   # kind -> (text, at): de-duplicate repeats
REPEAT_WINDOW = 10 * 60
KEEP_DAYS_UNJOURNALED = 7


def _conn():
    from backend.models.core import get_connection

    conn = get_connection()
    conn.execute(
        "CREATE TABLE IF NOT EXISTS experiences (id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT, day TEXT,"
        " kind TEXT, text TEXT)"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_experiences_day ON experiences(day)")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS journal (day TEXT PRIMARY KEY, entry TEXT, memories TEXT,"
        " created_at TEXT DEFAULT CURRENT_TIMESTAMP)"
    )
    return conn


# ---------------------------------------------------------------------------
# Experiences (cheap, local, all day)
# ---------------------------------------------------------------------------

def experience(kind: str, text: str) -> None:
    """Remember that something happened (skips a repeat within 10 minutes)."""
    text = re.sub(r"\s+", " ", str(text or "")).strip()[:300]
    if not text:
        return
    now = time.time()
    with _lock:
        last = _last_text.get(kind)
        if last and last[0] == text and now - last[1] < REPEAT_WINDOW:
            return
        _last_text[kind] = (text, now)
    local = datetime.now()
    try:
        conn = _conn()
        try:
            conn.execute("INSERT INTO experiences (at, day, kind, text) VALUES (?, ?, ?, ?)",
                         (local.isoformat(timespec="seconds"), local.date().isoformat(), kind, text))
            conn.commit()
        finally:
            conn.close()
    except Exception as exc:
        logger.debug("experience not saved: %s", exc)


def experiences(day: str, limit: int = 400) -> List[Dict[str, Any]]:
    try:
        conn = _conn()
        try:
            rows = conn.execute(
                "SELECT at, kind, text FROM experiences WHERE day = ? ORDER BY id LIMIT ?", (day, limit)
            ).fetchall()
        finally:
            conn.close()
        return [{"at": r[0], "kind": r[1], "text": r[2]} for r in rows]
    except Exception:
        return []


# ---------------------------------------------------------------------------
# The day's conversations
# ---------------------------------------------------------------------------

def _messages_of(day: str, limit: int = 160) -> List[Dict[str, Any]]:
    """Chat messages from a local calendar day (the DB stores UTC)."""
    start_local = datetime.fromisoformat(day)
    offset = datetime.now(timezone.utc).astimezone().utcoffset() or timedelta(0)
    start_utc = start_local - offset
    end_utc = start_utc + timedelta(days=1)
    try:
        from backend.models.core import get_connection

        conn = get_connection()
        try:
            rows = conn.execute(
                "SELECT role, content, created_at FROM messages WHERE created_at >= ? AND created_at < ?"
                " ORDER BY id LIMIT ?",
                (start_utc.strftime("%Y-%m-%d %H:%M:%S"), end_utc.strftime("%Y-%m-%d %H:%M:%S"), limit),
            ).fetchall()
        finally:
            conn.close()
        return [{"role": r[0], "content": r[1], "at": r[2]} for r in rows]
    except Exception:
        return []


def _digest(day: str, max_chars: int = 12000) -> str:
    from backend.embodiment import strip_body_tags
    from backend.identity import get_user_name

    user = get_user_name()
    lines = [f"Things you experienced on {day}:"]
    for e in experiences(day):
        lines.append(f"[{e['at'][11:16]}] ({e['kind']}) {e['text']}")
    msgs = _messages_of(day)
    if msgs:
        lines.append("")
        lines.append("What you and Zero said (trimmed):")
        for m in msgs:
            who = user if m["role"] == "user" else "You"
            text = re.sub(r"\s+", " ", strip_body_tags(m["content"] or ""))
            lines.append(f"{who}: {text[:220]}")
    text = "\n".join(lines)
    if len(text) > max_chars:  # keep the start and the end of the day
        text = text[: max_chars // 2] + "\n...\n" + text[-max_chars // 2:]
    return text


# ---------------------------------------------------------------------------
# Writing the journal
# ---------------------------------------------------------------------------

PROMPT = (
    "You are Sarah. Below is what happened on {day}. Write your private journal entry about that day "
    "in your own voice, first person, 80-180 words: what {user} was up to (games, projects, moods), "
    "what you did together or for them, how you felt, and anything to follow up on. Then list up to 6 "
    "short lasting facts worth remembering about {user} or your life together (plans, preferences, "
    "progress in games/projects, people, events). Skip trivia.\n"
    'Reply with JSON only: {{"entry": "...", "memories": ["...", "..."]}}\n\n{digest}'
)


def _parse(text: str) -> Dict[str, Any]:
    m = re.search(r"\{.*\}", text or "", re.S)
    if m:
        try:
            data = json.loads(m.group(0))
            if isinstance(data, dict) and data.get("entry"):
                mems = [str(x).strip() for x in (data.get("memories") or []) if str(x).strip()]
                return {"entry": str(data["entry"]).strip(), "memories": mems[:6]}
        except ValueError:
            pass
    text = (text or "").strip()
    return {"entry": text, "memories": []} if text else {}


def has_entry(day: str) -> bool:
    try:
        conn = _conn()
        try:
            return conn.execute("SELECT 1 FROM journal WHERE day = ?", (day,)).fetchone() is not None
        finally:
            conn.close()
    except Exception:
        return True  # can't tell: don't spend a request


def _remember(facts: List[str]) -> int:
    from backend.models.core import add_memory, get_connection

    added = 0
    for fact in facts:
        conn = get_connection()
        try:
            exists = conn.execute("SELECT 1 FROM memories WHERE lower(content) = lower(?)", (fact,)).fetchone()
        finally:
            conn.close()
        if not exists:
            add_memory("assistant", fact[:400], tags="journal", importance=1)
            added += 1
    return added


async def write_day(day: str, complete=None) -> Optional[Dict[str, Any]]:
    """Write (or rewrite) the journal entry for `day`. `complete` is an async
    prompt->text function (defaults to her model's simple completion)."""
    digest = _digest(day)
    if digest.count("\n") < 2:
        return None  # nothing happened that day
    if complete is None:
        from backend.state import get_sarah

        client = getattr(get_sarah(), "_openrouter", None)
        if client is None:
            return None
        complete = lambda prompt: client.simple_completion(prompt, max_tokens=900, temperature=0.6)
    from backend.identity import get_user_name
    from backend.usage import using

    with using("journal"):
        raw = await complete(PROMPT.format(day=day, user=get_user_name(), digest=digest))
    result = _parse(raw)
    if not result.get("entry"):
        logger.info("[JOURNAL] %s: no entry written", day)
        return None
    added = _remember(result["memories"])
    conn = _conn()
    try:
        conn.execute("INSERT OR REPLACE INTO journal (day, entry, memories) VALUES (?, ?, ?)",
                     (day, result["entry"], json.dumps(result["memories"])))
        conn.commit()
    finally:
        conn.close()
    logger.info("[JOURNAL] wrote %s (%d chars, %d new memories)", day, len(result["entry"]), added)
    return {"day": day, **result, "new_memories": added}


def days_needing_entry(today: Optional[date] = None) -> List[str]:
    today = today or date.today()
    out = []
    for back in range(1, KEEP_DAYS_UNJOURNALED + 1):
        day = (today - timedelta(days=back)).isoformat()
        if not has_entry(day) and (experiences(day, limit=1) or _messages_of(day, limit=1)):
            out.append(day)
    return out


async def journal_loop(interval: float = 30 * 60) -> None:
    """Write entries for finished days that don't have one (one per pass)."""
    await asyncio.sleep(90)
    while True:
        try:
            pending = days_needing_entry()
            if pending:
                await write_day(pending[0])
        except Exception as exc:
            logger.warning("[JOURNAL] loop error: %s", exc)
        await asyncio.sleep(interval)


# ---------------------------------------------------------------------------
# Carrying it into every prompt
# ---------------------------------------------------------------------------

def entries(limit: int = 30) -> List[Dict[str, Any]]:
    try:
        conn = _conn()
        try:
            rows = conn.execute("SELECT day, entry, memories FROM journal ORDER BY day DESC LIMIT ?", (limit,)).fetchall()
        finally:
            conn.close()
        return [{"day": r[0], "entry": r[1], "memories": json.loads(r[2] or "[]")} for r in rows]
    except Exception:
        return []


def render(recent_days: int = 2, today_lines: int = 8) -> str:
    """Her recent days (journal) + earlier today, for the system prompt."""
    parts = []
    for e in reversed(entries(recent_days)):
        label = datetime.fromisoformat(e["day"]).strftime("%A %d %B")
        parts.append(f"{label}: {e['entry']}")
    today = experiences(date.today().isoformat())
    if today:
        recent = [x for x in today if x["kind"] != "felt"][-today_lines:]
        if recent:
            parts.append("Earlier today: " + " · ".join(f"{x['at'][11:16]} {x['text']}" for x in recent))
    if not parts:
        return ""
    return "# Your recent days (your own memory)\n" + "\n".join(parts)
