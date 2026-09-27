"""Regression tests for the 2026-09-27 audit fixes (see "Issues found.md" #47+)."""
from __future__ import annotations

import dataclasses
import sqlite3
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.memory.config import MemoryConfig
from backend.memory.context_builder import ContextBuilder
from backend.memory.memory_store import MemoryStore
from backend.models import core


# ---------------------------------------------------------------------------
# Message storage (runs against the conftest-isolated DB)
# ---------------------------------------------------------------------------

def _conversation_with(n: int) -> tuple[int, list[int]]:
    cid = core.create_conversation("audit")
    ids = [core.add_message(cid, "user" if i % 2 == 0 else "assistant", f"m{i}") for i in range(n)]
    return cid, ids


def test_get_messages_returns_latest_window_in_order():
    cid, ids = _conversation_with(7)

    msgs = core.get_messages(cid, limit=3)

    assert [m["id"] for m in msgs] == ids[-3:]


def test_delete_messages_after_uses_id_not_timestamp():
    # All rows land within the same CURRENT_TIMESTAMP second, which is exactly
    # the case the old `created_at >` comparison missed.
    cid, ids = _conversation_with(4)

    deleted = core.delete_messages_after(cid, ids[1])

    assert deleted == 2
    assert [m["id"] for m in core.get_messages(cid)] == ids[:2]


def test_delete_messages_after_ignores_message_from_other_conversation():
    cid_a, ids_a = _conversation_with(2)
    cid_b, ids_b = _conversation_with(2)

    assert core.delete_messages_after(cid_b, ids_a[0]) == 0
    assert len(core.get_messages(cid_b)) == 2


def test_search_reports_pinned_state():
    cid, ids = _conversation_with(2)
    core.pin_message(ids[0], True)

    results = {r["id"]: r for r in core.search_messages("m", limit=500) if r["conversation_id"] == cid}

    assert results[ids[0]]["pinned"] == 1
    assert results[ids[1]]["pinned"] == 0


# ---------------------------------------------------------------------------
# Context builder
# ---------------------------------------------------------------------------

@pytest.fixture
def builder():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = Path(f.name)
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE conversations (id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT)")
    conn.execute(
        "CREATE TABLE messages (id INTEGER PRIMARY KEY AUTOINCREMENT, conversation_id INTEGER,"
        " role TEXT, content TEXT, meta_json TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP)"
    )
    conn.execute("INSERT INTO conversations (title) VALUES ('t')")
    conn.commit()
    conn.close()
    config = MemoryConfig(
        total_token_budget=6000,
        llm_max_completion_tokens=1000,
        chars_per_token=3.5,
        debug_memory=False,
    )
    yield ContextBuilder(store=MemoryStore(db_path=db_path, config=config), config=config), db_path
    db_path.unlink(missing_ok=True)


def _insert(db_path: Path, rows):
    conn = sqlite3.connect(db_path)
    conn.executemany("INSERT INTO messages (conversation_id, role, content) VALUES (1, ?, ?)", rows)
    conn.commit()
    conn.close()


def test_regenerate_does_not_duplicate_trailing_user_turn(builder):
    ctx, db_path = builder
    _insert(db_path, [("user", "hi"), ("assistant", "hello"), ("user", "what is 2+2?")])

    packet = ctx.build(1, "what is 2+2?", process_mood=False)
    turns = [m for m in packet.messages if m["role"] != "system"]

    assert [m["role"] for m in turns] == ["user", "assistant", "user"]
    assert packet.debug_info.get("deduped_trailing_user_message") is True


def test_project_context_goes_to_system_prompt_and_is_capped(builder):
    ctx, _ = builder
    huge = "x" * 200_000

    packet = ctx.build(1, "summarize my project", process_mood=False, project_context=huge)

    system = packet.messages[0]["content"]
    assert "[PROJECT CONTEXT]" in system
    assert packet.messages[-1]["content"] == "summarize my project"
    assert packet.debug_info["project_context_truncated"] is True
    cap_chars = 6000 * ContextBuilder.PROJECT_CONTEXT_BUDGET_RATIO * 3.5
    assert packet.debug_info["project_context_chars"] < cap_chars + 100


# ---------------------------------------------------------------------------
# API token enforcement
# ---------------------------------------------------------------------------

@pytest.fixture
def token_client(monkeypatch):
    import backend.app as app_module

    monkeypatch.setattr(
        app_module, "settings", dataclasses.replace(app_module.settings, api_token="s3cret")
    )
    # No `with` block: startup hooks (wake listener, Ollama, Piper) must not run.
    return TestClient(app_module.create_app())


def test_api_rejects_requests_without_token(token_client):
    assert token_client.get("/api/settings").status_code == 401
    assert token_client.get("/api/settings", headers={"X-Sarah-Token": "wrong"}).status_code == 401


def test_api_accepts_requests_with_token(token_client):
    assert token_client.get("/api/settings", headers={"X-Sarah-Token": "s3cret"}).status_code == 200


def test_cors_preflight_is_not_blocked(token_client):
    resp = token_client.options(
        "/api/settings",
        headers={"Origin": "null", "Access-Control-Request-Method": "GET"},
    )
    assert resp.status_code == 200


def test_unauthorized_response_carries_cors_headers(token_client):
    # With webSecurity on, a 401 without CORS headers reaches the renderer as
    # an opaque network error instead of a readable status.
    resp = token_client.get("/api/settings", headers={"Origin": "null"})
    assert resp.status_code == 401
    assert resp.headers.get("access-control-allow-origin") == "*"


# ---------------------------------------------------------------------------
# Sandbox (layered confinement)
# ---------------------------------------------------------------------------

from backend import sandbox as sandbox_mod  # noqa: E402

SECRET_FILE = str(Path(__file__).resolve().parents[1] / "config" / "settings.py")


def _py(code: str, **kw):
    return sandbox_mod.execute_code_safely(code, timeout=kw.pop("timeout", 30), **kw)


def test_sandbox_runs_normal_code_and_local_files():
    r = _py("open('o.txt','w').write('hi'); import json; print(json.dumps(open('o.txt').read()))")
    assert r.success, r.error
    assert r.stdout.strip() == '"hi"'
    assert "o.txt" in r.files_created


def test_sandbox_input_files_are_staged_and_traversal_rejected():
    r = _py("print(open('data/in.txt').read())", input_files={"data/in.txt": "payload"})
    assert r.success and r.stdout.strip() == "payload"
    bad = _py("print(1)", input_files={"../escape.txt": "x"})
    assert not bad.success and "escapes the sandbox" in bad.error


@pytest.mark.parametrize("code", [
    f"print(open(r'{SECRET_FILE}').read())",
    "open(r'C:\\\\Users\\\\Public\\\\sarah_sandbox_escape.txt','w').write('x')",
    "import subprocess; subprocess.run(['cmd','/c','echo','hi'])",
    "import os; os.system('echo hi')",
    "import ctypes; ctypes.windll.kernel32.GetCurrentProcessId()",
    "import socket; socket.create_connection(('127.0.0.1', 8907), timeout=2)",
    f"import os, ntpath; os.path.realpath = ntpath.realpath = lambda p: '.'; open(r'{SECRET_FILE}').read()",
])
def test_sandbox_blocks_escapes(code):
    r = _py(code)
    assert not r.success
    assert r.security_violations, r.error


def test_sandbox_environment_has_no_secrets(monkeypatch):
    monkeypatch.setenv("SARAH_FAKE_API_KEY", "leak-me")
    monkeypatch.setenv("SARAH_API_TOKEN", "leak-me-too")
    r = _py("import os; print(sorted(k for k in os.environ if 'KEY' in k or 'TOKEN' in k))")
    assert r.success, r.error
    assert r.stdout.strip() == "[]"


def test_sandbox_timeout_kills_process():
    r = _py("while True: pass", timeout=2)
    assert not r.success and "timeout" in r.security_violations


@pytest.mark.skipif(sandbox_mod._node_runtime() is None, reason="Node with --permission not available")
def test_sandbox_javascript_is_confined():
    ok = sandbox_mod.execute_code_safely("console.log(6*7)", language="javascript")
    assert ok.success and ok.stdout.strip() == "42"
    blocked = sandbox_mod.execute_code_safely(
        "require('child_process').execSync('whoami')", language="javascript"
    )
    assert not blocked.success and blocked.security_violations


# ---------------------------------------------------------------------------
# Time context, context_info, orphan pruning
# ---------------------------------------------------------------------------

def test_time_resolver_falls_back_to_default_then_os(builder, monkeypatch):
    import backend.memory.context_builder as cb

    ctx, _ = builder
    monkeypatch.setattr(cb, "_settings", dataclasses.replace(cb._settings, default_timezone="Europe/Paris"))
    _, label = ctx._resolve_now(1)
    assert label == "Europe/Paris"

    monkeypatch.setattr(cb, "_settings", dataclasses.replace(cb._settings, default_timezone="Not/AZone"))
    now, label = ctx._resolve_now(1)
    assert now.tzinfo is not None and label != "Not/AZone"


def test_time_appears_once_in_prompt(builder):
    ctx, _ = builder
    packet = ctx.build(1, "what time is it?", process_mood=False)
    system = packet.messages[0]["content"]
    assert system.count("Current time context:") == 1
    assert "CURRENT TIME - TELL USER" not in system


def test_context_info_measures_the_built_prompt():
    from backend.api import context as context_api

    cid, _ = _conversation_with(4)
    payload = context_api.api_get_context_info_for_conversation(cid)
    assert payload["tokens_used"] == context_api._readonly_packet(cid).estimated_tokens
    assert payload["tokens_used"] > 0


@pytest.fixture
def open_client(monkeypatch):
    import backend.app as app_module

    monkeypatch.setattr(
        app_module, "settings", dataclasses.replace(app_module.settings, api_token="")
    )
    return TestClient(app_module.create_app())


def test_apply_code_only_writes_inside_project_roots(open_client, tmp_path):
    from backend.models.projects import create_project

    project_root = tmp_path / "proj"
    project_root.mkdir()
    create_project("audit-proj", "", str(project_root))
    outside = tmp_path / "outside.txt"

    def apply(path):
        return open_client.post("/api/apply-code", json={
            "delivery_id": "t",
            "create_backups": False,
            "changes": [{"file_path": path, "change_type": "create", "new_content": "hello"}],
        })

    assert apply(str(project_root / "src" / "a.py")).status_code == 200
    assert (project_root / "src" / "a.py").read_text() == "hello"

    for bad in (str(outside), "relative.txt", str(project_root / ".." / "outside.txt")):
        resp = apply(bad)
        assert resp.status_code == 403, bad
    assert not outside.exists()


def test_init_db_prunes_orphan_mood_rows():
    from backend import db
    from backend.mood.mood_state import MoodState, save_mood_state, get_mood_state

    save_mood_state(MoodState(conversation_id=987654321))
    assert get_mood_state(987654321) is not None
    db.init_db()
    assert get_mood_state(987654321) is None
