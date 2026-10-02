"""A chat picked up again on a later day: she knows the earlier part was then."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

from backend.memory.config import MemoryConfig
from backend.memory.context_builder import ContextBuilder
from backend.memory.memory_store import MemoryStore
from backend.reply_sanitizer import sanitize_visible_reply


def _db(tmp_path, rows):
    db = tmp_path / "mem.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE conversations (id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP)")
    conn.execute("CREATE TABLE messages (id INTEGER PRIMARY KEY AUTOINCREMENT, conversation_id INTEGER, role TEXT, content TEXT, meta_json TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP)")
    conn.execute("INSERT INTO conversations (title) VALUES ('t')")
    for role, content, hours_ago in rows:
        at = (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).strftime("%Y-%m-%d %H:%M:%S")
        conn.execute("INSERT INTO messages (conversation_id, role, content, created_at) VALUES (1, ?, ?, ?)", (role, content, at))
    conn.commit()
    conn.close()
    config = MemoryConfig(total_token_budget=8000, llm_max_completion_tokens=1000, debug_memory=False)
    return ContextBuilder(store=MemoryStore(db_path=db, config=config), config=config)


def test_next_day_is_marked_in_prompt_history_and_new_message(tmp_path):
    builder = _db(tmp_path, [("user", "Good night, talk tomorrow!", 50), ("assistant", "Sleep well!", 49.9)])
    packet = builder.build(1, "Morning! What did I say last night?")
    system = packet.messages[0]["content"]
    assert "last message before now" in system and "earlier day" in system
    user_turns = [m["content"] for m in packet.messages if m["role"] == "user"]
    assert user_turns[0].startswith("[") and user_turns[0].endswith("Good night, talk tomorrow!")
    assert user_turns[-1].startswith("[") and user_turns[-1].endswith("What did I say last night?")
    assistant = [m["content"] for m in packet.messages if m["role"] == "assistant"]
    assert assistant == ["Sleep well!"]  # her replies never carry markers


def test_same_session_carries_no_markers(tmp_path):
    builder = _db(tmp_path, [("user", "hey", 0.05), ("assistant", "hi!", 0.04)])
    packet = builder.build(1, "how are you?")
    system = packet.messages[0]["content"]
    assert "earlier day" not in system and "last message before now" in system
    assert not any(m["content"].startswith("[") for m in packet.messages[1:])


def test_echoed_marker_is_stripped_from_her_reply():
    assert sanitize_visible_reply("[Fri, Oct 3, 9:12 AM] Good morning!") == "Good morning!"
    assert sanitize_visible_reply("[Note] stays") == "[Note] stays"
