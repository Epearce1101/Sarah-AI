"""Sarah knows where her own files are, may read them, and may not change them."""
from __future__ import annotations

import asyncio
import os
import sqlite3

import pytest

from backend.agency import guard, tools
from backend.config.settings import REPO_ROOT
from backend.memory.config import MemoryConfig
from backend.memory.context_builder import ContextBuilder
from backend.memory.memory_store import MemoryStore
from backend.self_knowledge import build_self_block


def test_block_points_at_her_real_install():
    block = build_self_block()
    assert str(REPO_ROOT) in block
    assert "never change it" in block


def test_every_prompt_carries_it(tmp_path):
    db = tmp_path / "mem.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE conversations (id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP)")
    conn.execute("CREATE TABLE messages (id INTEGER PRIMARY KEY AUTOINCREMENT, conversation_id INTEGER, role TEXT, content TEXT, meta_json TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP)")
    conn.execute("INSERT INTO conversations (title) VALUES ('t')")
    conn.commit()
    conn.close()
    config = MemoryConfig(total_token_budget=8000, llm_max_completion_tokens=1000, debug_memory=False)
    builder = ContextBuilder(store=MemoryStore(db_path=db, config=config), config=config)
    assert "# Yourself" in builder.build(1, "How do you remember things?").messages[0]["content"]


def test_she_can_read_her_own_files():
    out = asyncio.run(tools.call("read_file", {"path": str(REPO_ROOT / "README.md")}))
    assert out["ok"] and "Sarah" in out["result"]


@pytest.mark.skipif(os.name != "nt", reason="the guard matches Windows paths")
def test_she_cannot_write_her_own_files():
    readme = REPO_ROOT / "README.md"
    with pytest.raises(guard.Blocked):
        guard.check_write(readme)
    with pytest.raises(guard.Blocked):
        guard.check_write(REPO_ROOT / "Backend" / "backend" / "agency" / "tools.py")
    # Her workspace stays hers.
    guard.check_write(guard.WORKSPACE / "notes.txt")
