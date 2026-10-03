"""Shared pytest fixtures for the backend test suite."""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

import backend.db as _db
from backend.persona.state import PersonaSnapshot, set_persona


@pytest.fixture(scope="session", autouse=True)
def _isolate_database():
    """Redirect the process-global SQLite path at an isolated temp DB.

    Several modules (skills runtime, mood state, diagnostics telemetry) persist
    to ``backend.db.DB_PATH`` via ``get_connection()``/late-bound imports. Tests
    that exercise them (``test_skills``, ``test_mood_system``,
    ``test_diagnostics_telemetry``) were writing into the real
    ``data/sarah.db`` — leaving phantom "stale" skill rows and accumulating junk
    that bloated the production database. Pointing ``DB_PATH`` at a throwaway
    file (created once for the whole session) keeps those writes out of real
    data. Session scope mirrors the previous shared-DB behaviour those tests
    already tolerate, without the per-test setup cost. Tests that pass an
    explicit ``db_path`` are unaffected.
    """
    tmp_dir = Path(tempfile.mkdtemp(prefix="sarah_test_db_"))
    db_path = tmp_dir / "test_sarah.db"
    original = _db.DB_PATH
    _db.DB_PATH = db_path
    _db.init_db()
    try:
        yield
    finally:
        _db.DB_PATH = original
        for leftover in tmp_dir.glob("*"):
            leftover.unlink(missing_ok=True)
        try:
            tmp_dir.rmdir()
        except OSError:
            pass


@pytest.fixture(autouse=True)
def _isolate_persona_state():
    """Reset the process-global persona snapshot around every test.

    Some tests exercise the persona loader (e.g.
    ``test_backlog_followups.test_persona_switch_writes_active_state`` calls
    ``switch_persona``), which mutates a process-global snapshot via
    ``set_persona``. Without an explicit reset that snapshot leaks into later
    tests — most visibly the context-builder budget tests, where an injected
    persona block silently consumes the token budget and trips the
    emergency-trim path. Resetting before and after each test keeps budget math
    deterministic while leaving tests that load their own persona untouched.
    """
    set_persona(PersonaSnapshot())
    yield
    set_persona(PersonaSnapshot())


@pytest.fixture(autouse=True)
def _isolate_sarah_workspace_state(tmp_path, monkeypatch):
    """Her agenda and reminders live in her real workspace; tests must never
    read or write them (a live test once left a fictional "interview"
    follow-up in the real agenda)."""
    from backend.agency import agenda, plans, reminders

    monkeypatch.setattr(agenda, "_FILE", tmp_path / "agenda.json")
    monkeypatch.setattr(reminders, "_FILE", tmp_path / "reminders.json")
    monkeypatch.setattr(plans, "_FILE", tmp_path / "plans.json")
    from backend import tts_kokoro
    monkeypatch.setattr(tts_kokoro, "_prefs_file", lambda: tmp_path / "voice.json")  # not Zero's voice choice


def fake_embed(texts):
    """Bag-of-words vectors: related texts share words -> high cosine. Keeps
    tests off the real embedding model."""
    import re
    import zlib

    import numpy as np

    out = np.zeros((len(texts), 384), dtype=np.float32)
    for i, text in enumerate(texts):
        text = text.replace("Represent this sentence for searching relevant passages: ", "")
        for word in re.findall(r"[a-z]{3,}", text.lower()):
            out[i, zlib.crc32(word.encode()) % 384] += 1.0
        out[i, 383] += 0.01
    return out / np.linalg.norm(out, axis=1, keepdims=True)


@pytest.fixture(autouse=True)
def _no_smart_turn_model(monkeypatch):
    """Tests use the silence/words rules unless they fake Smart Turn themselves."""
    from backend.voice import smart_turn

    monkeypatch.setattr(smart_turn, "probability", lambda audio: None)


@pytest.fixture(autouse=True)
def _isolate_episodic(monkeypatch):
    """Episodic memory writes inline with a fake embedder (no model, no thread)."""
    from backend.memory import episodic

    monkeypatch.setattr(episodic, "SYNC", True)
    monkeypatch.setattr(episodic, "MIN_SCORE", 0.2)  # bag-of-words scores run lower
    episodic.set_embedder(fake_embed)
    yield
    episodic.set_embedder(fake_embed)


@pytest.fixture(autouse=True)
def _fresh_tool_safety():
    """Each test starts with no failures counted and nothing awaiting Zero's OK."""
    from backend.agency import safety

    safety.reset_failures()
    safety._pending.clear()
    yield
    safety.reset_failures()
    safety._pending.clear()
