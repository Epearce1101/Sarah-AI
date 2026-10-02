"""Zero's notes for Sarah (Functions tab): saved once, in every prompt at once."""
from __future__ import annotations

import sqlite3

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend import user_notes
from backend.api.settings import router as settings_router
from backend.memory.config import MemoryConfig
from backend.memory.context_builder import ContextBuilder
from backend.memory.memory_store import MemoryStore


@pytest.fixture(autouse=True)
def _clear_notes():
    user_notes.save_notes("")
    yield
    user_notes.save_notes("")


@pytest.fixture
def builder_and_conv(tmp_path):
    db = tmp_path / "mem.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE conversations (id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP)")
    conn.execute("CREATE TABLE messages (id INTEGER PRIMARY KEY AUTOINCREMENT, conversation_id INTEGER, role TEXT, content TEXT, meta_json TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP)")
    conn.execute("INSERT INTO conversations (title) VALUES ('t')")
    conn.commit()
    conn.close()
    config = MemoryConfig(total_token_budget=8000, llm_max_completion_tokens=1000, debug_memory=False)
    return ContextBuilder(store=MemoryStore(db_path=db, config=config), config=config), 1


def _system(builder, conv):
    return builder.build(conv, "Hello").messages[0]["content"]


def test_no_notes_no_block(builder_and_conv):
    builder, conv = builder_and_conv
    assert "Zero's notes for you" not in _system(builder, conv)


def test_saved_notes_apply_on_the_next_build(builder_and_conv):
    builder, conv = builder_and_conv
    before = _system(builder, conv)
    user_notes.save_notes("Keep answers short.\nMy cat is called Miso.")
    after = _system(builder, conv)
    assert "Zero's notes for you" not in before
    assert "Zero's notes for you" in after
    assert "My cat is called Miso." in after
    # Cleared notes leave the prompt again, with no restart.
    user_notes.save_notes("   ")
    assert "Zero's notes for you" not in _system(builder, conv)


def test_api_round_trip_and_limit():
    app = FastAPI()
    app.include_router(settings_router)
    client = TestClient(app)
    assert client.get("/api/user_notes").json()["notes"] == ""
    saved = client.post("/api/user_notes", json={"notes": "  Call me Zero.\r\n  "}).json()
    assert saved["ok"] and saved["notes"] == "Call me Zero." and saved["updated_at"]
    got = client.get("/api/user_notes").json()
    assert got["notes"] == "Call me Zero." and got["max_chars"] == user_notes.MAX_CHARS
    too_long = client.post("/api/user_notes", json={"notes": "x" * (user_notes.MAX_CHARS + 1)})
    assert too_long.status_code == 400
    assert client.get("/api/user_notes").json()["notes"] == "Call me Zero."
