import difflib
import re
import sqlite3
from typing import Dict, Optional, List, Any
from backend.db import get_connection


# ============================================================
# SETTINGS
# ============================================================

def get_setting(key: str) -> Optional[str]:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT value FROM settings WHERE key = ?", (key,))
    row = cur.fetchone()
    conn.close()
    return row["value"] if row else None


def set_setting(key: str, value: str):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO settings (key, value, updated_at)
        VALUES (?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(key) DO UPDATE SET
            value = excluded.value,
            updated_at = CURRENT_TIMESTAMP;
        """,
        (key, value),
    )
    conn.commit()
    conn.close()


def get_all_settings() -> Dict[str, str]:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT key, value FROM settings;")
    rows = cur.fetchall()
    conn.close()
    return {row["key"]: row["value"] for row in rows}


# ============================================================
# LONG-TERM MEMORY
# ============================================================

def add_memory(
    role: str,
    content: str,
    tags: str = "",
    importance: int = 0,
    embedding: Optional[bytes] = None,
):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO memories (role, content, tags, importance, embedding, created_at)
        VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        """,
        (role, content, tags, importance, embedding),
    )
    memory_id = cur.lastrowid
    conn.commit()
    conn.close()
    try:
        from backend.memory import episodic
        episodic.note("fact", content, source=f"mem:{memory_id}", importance=1)
    except Exception:
        pass
    return memory_id


def get_memories(limit: int = 50) -> List[Dict[str, Any]]:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT id, role, content, tags, importance, created_at, last_used_at
        FROM memories
        ORDER BY created_at DESC
        LIMIT ?
        """,
        (limit,),
    )
    rows = cur.fetchall()
    conn.close()
    return [dict(row) for row in rows]


_STOPWORDS = frozenset(
    "a an and are as at be but by can do for from have how i if in is it me my "
    "of on or so that the this to was we what when where which who why will with "
    "you your about just like please tell know".split()
)


def _fts_terms(text: str, *, drop_stopwords: bool = False, limit: int = 12) -> List[str]:
    seen: List[str] = []
    for tok in re.findall(r"\w+", (text or "").lower()):
        if drop_stopwords and (tok in _STOPWORDS or len(tok) < 3):
            continue
        if tok not in seen:
            seen.append(tok)
    return seen[:limit]


def _fts_query(text: str, *, any_term: bool = False) -> str:
    """Safe FTS5 MATCH expression: every term quoted, prefix-matched."""
    terms = _fts_terms(text, drop_stopwords=any_term)
    joiner = " OR " if any_term else " "
    return joiner.join(f'"{t}"*' for t in terms)


def search_memories(keyword: str) -> List[Dict[str, Any]]:
    conn = get_connection()
    cur = conn.cursor()
    query = _fts_query(keyword)
    try:
        if not query:
            raise sqlite3.OperationalError("empty query")
        cur.execute(
            """
            SELECT m.* FROM memories_fts f JOIN memories m ON m.id = f.rowid
            WHERE memories_fts MATCH ?
            ORDER BY m.importance DESC, bm25(memories_fts)
            """,
            (query,),
        )
    except sqlite3.OperationalError:
        like = f"%{keyword}%"
        cur.execute(
            """
            SELECT * FROM memories
            WHERE content LIKE ? OR tags LIKE ?
            ORDER BY importance DESC, created_at DESC
            """,
            (like, like),
        )
    rows = cur.fetchall()
    conn.close()
    return [dict(row) for row in rows]


def find_relevant_memories(text: str, limit: int = 6) -> List[Dict[str, Any]]:
    """Memories sharing meaningful words with `text`, best matches first."""
    query = _fts_query(text, any_term=True)
    if not query:
        return []
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT m.id, m.content, m.tags, m.importance, m.created_at
            FROM memories_fts f JOIN memories m ON m.id = f.rowid
            WHERE memories_fts MATCH ?
            ORDER BY bm25(memories_fts)
            LIMIT ?
            """,
            (query, limit),
        ).fetchall()
    except sqlite3.OperationalError:
        rows = []
    finally:
        conn.close()
    return [dict(r) for r in rows]


def _normalize_fact(text: str) -> str:
    return " ".join(re.findall(r"\w+", (text or "").lower()))


def add_auto_memories(facts: List[str], similarity: float = 0.85) -> int:
    """Store extracted long-term facts, skipping near-duplicates. Returns count added."""
    cleaned = []
    for fact in facts or []:
        if not isinstance(fact, str):
            continue
        fact = " ".join(fact.split())
        if 8 <= len(fact) <= 240:
            cleaned.append(fact)
    if not cleaned:
        return 0

    conn = get_connection()
    try:
        existing = [
            _normalize_fact(r["content"])
            for r in conn.execute("SELECT content FROM memories ORDER BY id DESC LIMIT 500")
        ]
        added = 0
        for fact in cleaned:
            norm = _normalize_fact(fact)
            if any(norm == e or difflib.SequenceMatcher(None, norm, e).ratio() >= similarity for e in existing):
                continue
            conn.execute(
                """
                INSERT INTO memories (role, content, tags, importance, created_at)
                VALUES ('fact', ?, 'auto', 2, CURRENT_TIMESTAMP)
                """,
                (fact,),
            )
            existing.append(norm)
            added += 1
        conn.commit()
        return added
    finally:
        conn.close()


def delete_memory(memory_id: int) -> bool:
    conn = get_connection()
    try:
        cur = conn.execute("DELETE FROM memories WHERE id = ?", (memory_id,))
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def touch_memories(memory_ids: List[int]) -> None:
    """Record that memories were injected into a prompt."""
    if not memory_ids:
        return
    conn = get_connection()
    try:
        conn.executemany(
            "UPDATE memories SET last_used_at = CURRENT_TIMESTAMP WHERE id = ?",
            [(i,) for i in memory_ids],
        )
        conn.commit()
    finally:
        conn.close()


# ---------- Pinned memories (importance >= 5 or tag includes 'pinned') ----------

def get_pinned_memories(limit: int = 100) -> List[Dict[str, Any]]:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT * FROM memories
        WHERE importance >= 5 OR tags LIKE '%pinned%'
        ORDER BY created_at DESC
        LIMIT ?
        """,
        (limit,),
    )
    rows = cur.fetchall()
    conn.close()
    return [dict(row) for row in rows]


def set_memory_pinned(memory_id: int, pinned: bool):
    conn = get_connection()
    cur = conn.cursor()

    # Fetch current tags/importance
    cur.execute("SELECT tags, importance FROM memories WHERE id = ?", (memory_id,))
    row = cur.fetchone()
    if not row:
        conn.close()
        return

    tags = row["tags"] or ""
    importance = row["importance"] or 0

    tags_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else []

    if pinned:
        if "pinned" not in tags_list:
            tags_list.append("pinned")
        if importance < 5:
            importance = 5
    else:
        tags_list = [t for t in tags_list if t != "pinned"]
        if importance >= 5:
            importance = 1

    new_tags = ",".join(tags_list)

    cur.execute(
        """
        UPDATE memories
        SET tags = ?, importance = ?, last_used_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (new_tags, importance, memory_id),
    )
    conn.commit()
    conn.close()


# ============================================================
# CONVERSATIONS
# ============================================================

def create_conversation(title: str = None) -> int:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO conversations (title, created_at, last_active_at)
        VALUES (?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
        """,
        (title,),
    )
    conversation_id = cur.lastrowid
    conn.commit()
    conn.close()
    return conversation_id


def list_conversations() -> List[Dict[str, Any]]:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT id, title, created_at, last_active_at
        FROM conversations
        ORDER BY last_active_at DESC
        """
    )
    rows = cur.fetchall()
    conn.close()
    return [dict(row) for row in rows]


def rename_conversation(conversation_id: int, title: str):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        UPDATE conversations
        SET title = ?, last_active_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (title, conversation_id),
    )
    conn.commit()
    conn.close()


def add_message(conversation_id: int, role: str, content: str, meta_json: str = None) -> int:
    """
    Add a message to a conversation.
    Returns the ID of the inserted message.
    """
    if role == "assistant":
        try:  # her body tags in their real form (she sometimes writes <feel=...>)
            from backend.embodiment.self_model import normalize_body_tags
            content = normalize_body_tags(content)
        except Exception:
            pass
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO messages (conversation_id, role, content, meta_json, created_at)
        VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
        """,
        (conversation_id, role, content, meta_json),
    )

    message_id = cur.lastrowid  # Get the inserted message ID

    cur.execute(
        """
        UPDATE conversations
        SET last_active_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (conversation_id,),
    )

    conn.commit()
    conn.close()
    if role == "assistant":
        try:  # the exchange becomes one of her episodic memories (background)
            from backend.memory import episodic
            episodic.note_message(message_id)
        except Exception:
            pass
    return message_id


def get_messages(conversation_id: int, limit: int = 200) -> List[Dict[str, Any]]:
    """Return the most recent `limit` messages, oldest first.

    Selecting newest-first then reversing matters once a conversation exceeds
    `limit`: an ascending LIMIT returned the *oldest* rows, so the chat view
    and the regenerate lookup never saw the latest turns.
    """
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT id, role, content, meta_json, created_at
        FROM messages
        WHERE conversation_id = ?
        ORDER BY id DESC
        LIMIT ?
        """,
        (conversation_id, limit),
    )
    rows = cur.fetchall()
    conn.close()
    return [dict(row) for row in reversed(rows)]


def update_message(message_id: int, content: str) -> bool:
    """Update a message's content."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "UPDATE messages SET content = ? WHERE id = ?",
        (content, message_id),
    )
    success = cur.rowcount > 0
    conn.commit()
    conn.close()
    return success


def delete_message(message_id: int) -> bool:
    """Delete a message by ID."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM messages WHERE id = ?", (message_id,))
    success = cur.rowcount > 0
    conn.commit()
    conn.close()
    return success


def delete_messages_after(conversation_id: int, message_id: int) -> int:
    """Delete all messages after a given message ID (for regeneration).

    Compares by id, not created_at: CURRENT_TIMESTAMP has one-second
    resolution, so a reply saved in the same second as the user turn was
    silently kept and the regenerated answer was appended next to it.
    """
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT 1 FROM messages WHERE id = ? AND conversation_id = ?",
        (message_id, conversation_id),
    )
    if not cur.fetchone():
        conn.close()
        return 0

    cur.execute(
        """
        DELETE FROM messages
        WHERE conversation_id = ? AND id > ?
        """,
        (conversation_id, message_id),
    )
    deleted_count = cur.rowcount
    conn.commit()
    conn.close()
    return deleted_count


def search_messages(query: str, limit: int = 50) -> List[Dict[str, Any]]:
    """Search messages across all conversations, newest first.

    Uses the FTS5 index (word/prefix matching) when available; an empty
    query lists recent messages; LIKE is the fallback.
    """
    conn = get_connection()
    cur = conn.cursor()
    columns = """
        SELECT m.id, m.conversation_id, m.role, m.content, m.created_at,
               COALESCE(m.pinned, 0) AS pinned,
               c.title as conversation_title
    """
    fts = _fts_query(query)
    try:
        if not fts:
            raise sqlite3.OperationalError("no terms")
        cur.execute(
            columns + """
            FROM messages_fts f
            JOIN messages m ON m.id = f.rowid
            JOIN conversations c ON m.conversation_id = c.id
            WHERE messages_fts MATCH ?
            ORDER BY m.id DESC
            LIMIT ?
            """,
            (fts, limit),
        )
    except sqlite3.OperationalError:
        cur.execute(
            columns + """
            FROM messages m
            JOIN conversations c ON m.conversation_id = c.id
            WHERE m.content LIKE ?
            ORDER BY m.id DESC
            LIMIT ?
            """,
            (f"%{query}%", limit),
        )
    rows = cur.fetchall()
    conn.close()
    return [dict(row) for row in rows]


def pin_message(message_id: int, pinned: bool = True) -> bool:
    """Pin or unpin a message."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "UPDATE messages SET pinned = ? WHERE id = ?",
        (1 if pinned else 0, message_id),
    )
    success = cur.rowcount > 0
    conn.commit()
    conn.close()
    return success


def get_pinned_messages(limit: int = 50) -> List[Dict[str, Any]]:
    """Get all pinned messages."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT m.id, m.conversation_id, m.role, m.content, m.created_at,
               c.title as conversation_title
        FROM messages m
        JOIN conversations c ON m.conversation_id = c.id
        WHERE m.pinned = 1
        ORDER BY m.id DESC
        LIMIT ?
        """,
        (limit,),
    )
    rows = cur.fetchall()
    conn.close()
    return [dict(row) for row in rows]


# ============================================================
# SKILLS / PLUGINS
# ============================================================

def register_skill(
    name: str,
    slug: str,
    description: str = "",
    enabled: bool = True,
    config_json: str = "{}",
):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO skills (name, slug, description, enabled, config_json, created_at)
        VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(slug) DO UPDATE SET
            name = excluded.name,
            description = excluded.description,
            enabled = excluded.enabled,
            config_json = excluded.config_json,
            updated_at = CURRENT_TIMESTAMP;
        """,
        (name, slug, description, 1 if enabled else 0, config_json),
    )
    conn.commit()
    conn.close()


def get_all_skills() -> List[Dict[str, Any]]:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM skills;")
    rows = cur.fetchall()
    conn.close()
    return [dict(row) for row in rows]


def set_skill_enabled(slug: str, enabled: bool):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        UPDATE skills
        SET enabled = ?, updated_at = CURRENT_TIMESTAMP
        WHERE slug = ?
        """,
        (1 if enabled else 0, slug),
    )
    conn.commit()
    conn.close()


def register_skill_from_disk(
    slug: str,
    name: str,
    description: str,
    path: str,
    enabled_default: bool = True,
) -> None:
    """Upsert a disk-discovered skill.

    Preserves the existing `enabled` flag if the row already exists; uses
    `enabled_default` only on first insert. Always refreshes `name`,
    `description`, and `path` from the manifest.
    """
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO skills (name, slug, description, enabled, path, created_at)
        VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(slug) DO UPDATE SET
            name = excluded.name,
            description = excluded.description,
            path = excluded.path,
            updated_at = CURRENT_TIMESTAMP;
        """,
        (name, slug, description, 1 if enabled_default else 0, path),
    )
    conn.commit()
    conn.close()


# ============================================================
# LOGGING (SQL)
# ============================================================

def add_log(level: str, message: str, source: str = None, payload_json: str = None):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO logs (level, source, message, payload_json, created_at)
        VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
        """,
        (level, source, message, payload_json),
    )
    conn.commit()
    conn.close()


def get_logs(limit: int = 200) -> List[Dict[str, Any]]:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT * FROM logs
        ORDER BY created_at DESC
        LIMIT ?
        """,
        (limit,),
    )
    rows = cur.fetchall()
    conn.close()
    return [dict(row) for row in rows]
