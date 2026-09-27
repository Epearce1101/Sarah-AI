"""Tests for the 2026-09-27 improvement pass (fallbacks, streaming, memory, ...)."""
from __future__ import annotations

import dataclasses

import pytest

from backend import llm_models

CATALOG = [
    {"id": "vendor/primary:free", "context_length": 1000000, "pricing": {"prompt": "0", "completion": "0"},
     "architecture": {"input_modalities": ["text"], "output_modalities": ["text"]}},
    {"id": "vendor/fallback-a", "context_length": 262144, "pricing": {"prompt": "0.000001", "completion": "0.000002"},
     "architecture": {"input_modalities": ["text", "image"], "output_modalities": ["text"]}},
    {"id": "vendor/fallback-b:free", "context_length": 200000, "pricing": {"prompt": "0", "completion": "0"},
     "architecture": {"input_modalities": ["text"], "output_modalities": ["text"]}},
    {"id": "vendor/music", "context_length": 1000, "pricing": {"prompt": "0", "completion": "0"},
     "architecture": {"input_modalities": ["text"], "output_modalities": ["audio"]}},
]


@pytest.fixture
def models_env(monkeypatch):
    monkeypatch.setattr(llm_models, "settings", dataclasses.replace(
        llm_models.settings,
        openrouter_model="vendor/primary:free",
        openrouter_fallback_models=("vendor/primary:free", "vendor/fallback-a", "vendor/fallback-b:free", "vendor/extra"),
        openrouter_vision_model="vendor/fallback-a",
    ))
    monkeypatch.setattr(llm_models, "fetch_catalog", lambda max_age=0: CATALOG)
    monkeypatch.setattr(llm_models, "_override", None)
    monkeypatch.setattr(llm_models, "_listeners", [])
    monkeypatch.setattr(llm_models, "_status", {"model": None, "available": None, "checked_at": None, "error": None})
    yield


def test_request_carries_primary_plus_at_most_two_fallbacks(models_env):
    kw = llm_models.completion_kwargs()
    assert kw["model"] == "vendor/primary:free"
    assert kw["extra_body"]["models"] == ["vendor/primary:free", "vendor/fallback-a", "vendor/fallback-b:free"]
    assert len(kw["extra_body"]["models"]) <= llm_models.MAX_MODELS_PER_REQUEST


def test_pinned_model_gets_no_fallbacks(models_env):
    kw = llm_models.completion_kwargs(model="vendor/fallback-a", reasoning=False)
    assert kw == {"model": "vendor/fallback-a", "extra_body": None}


def test_set_online_model_validates_persists_and_notifies(models_env):
    from backend.models.core import get_setting

    seen = []
    llm_models.on_model_change(seen.append)
    with pytest.raises(ValueError):
        llm_models.set_online_model("not a model id")
    with pytest.raises(ValueError):
        llm_models.set_online_model("vendor/not-in-catalog")

    llm_models.set_online_model("vendor/fallback-b:free")
    assert llm_models.current_online_model() == "vendor/fallback-b:free"
    assert get_setting(llm_models.MODEL_SETTING_KEY) == "vendor/fallback-b:free"
    assert seen == ["vendor/fallback-b:free"]
    # The new primary is not repeated among its own fallbacks.
    assert "vendor/fallback-b:free" not in llm_models.fallback_models()

    llm_models._override = None
    llm_models.load_persisted_model()
    assert llm_models.current_online_model() == "vendor/fallback-b:free"


def test_unavailable_model_surfaces_a_warning(models_env, monkeypatch):
    monkeypatch.setattr(llm_models, "fetch_catalog", lambda max_age=0: CATALOG[1:])
    status = llm_models.check_availability()
    assert status["available"] is False
    fields = llm_models.status_fields()
    assert fields["model_available"] is False
    assert "vendor/primary:free" in fields["model_warning"]


def test_live_404_marks_model_unavailable(models_env):
    llm_models.mark_unavailable("vendor/primary:free", "404 No endpoints found")
    assert llm_models.status_fields()["model_available"] is False


def test_catalog_summary_is_text_only_and_free_first(models_env):
    ids = [m["id"] for m in llm_models.catalog_summary()]
    assert "vendor/music" not in ids
    assert ids[:2] == ["vendor/fallback-b:free", "vendor/primary:free"]
    vision = {m["id"]: m["vision"] for m in llm_models.catalog_summary()}
    assert vision["vendor/fallback-a"] is True


@pytest.fixture
def memory_store(tmp_path):
    import sqlite3
    from backend.memory.config import MemoryConfig
    from backend.memory.memory_store import MemoryStore

    db = tmp_path / "mem.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE conversations (id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT)")
    conn.execute(
        "CREATE TABLE messages (id INTEGER PRIMARY KEY AUTOINCREMENT, conversation_id INTEGER,"
        " role TEXT, content TEXT, meta_json TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP)"
    )
    conn.execute("INSERT INTO conversations (title) VALUES ('t')")
    conn.commit()
    conn.close()
    config = MemoryConfig(debug_memory=False, task_state_every_n_turns=4)
    return MemoryStore(db_path=db, config=config), config


def test_task_state_extraction_is_skipped_for_plain_replies(memory_store):
    import asyncio
    from backend.memory.summarizer import Summarizer

    store, config = memory_store
    calls = []

    async def fake_llm(prompt, max_tokens):
        calls.append(prompt)
        return '{"pending_question": "", "pending_choices": [], "next_step": "", "current_task": "", "last_assistant_action": "x"}'

    summ = Summarizer(llm_call_fn=fake_llm, store=store, config=config)
    for _ in range(8):
        asyncio.run(summ.update_all(1, "Sure, done. Here is the answer."))
    # Every 4th turn only (turns 4 and 8), not all 8.
    assert len(calls) == 2
    assert store.get_task_state(1).turn_count == 8


@pytest.mark.parametrize("reply", [
    "Which one would you like?",
    "Options:\n1. Fast path\n2. Safe path",
])
def test_task_state_extraction_runs_for_questions_and_choices(memory_store, reply):
    from backend.memory.summarizer import Summarizer

    store, config = memory_store
    summ = Summarizer(llm_call_fn=None, store=store, config=config)
    assert summ.needs_task_state_update(1, reply) is True
    assert summ.needs_task_state_update(1, "All set.") is False


def test_vision_body_has_token_floor_and_vision_model(models_env):
    body = llm_models.vision_request_body([{"role": "user", "content": "x"}], max_tokens=96)
    assert body["model"] == "vendor/fallback-a"
    assert body["max_tokens"] >= llm_models.VISION_MIN_COMPLETION_TOKENS
