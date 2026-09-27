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


# ---------------------------------------------------------------------------
# Streaming
# ---------------------------------------------------------------------------

from types import SimpleNamespace as NS  # noqa: E402


class _FakeStream:
    def __init__(self, chunks):
        self._chunks = chunks
        self.closed = False

    def __iter__(self):
        return iter(self._chunks)

    def close(self):
        self.closed = True


class _FakeCompletions:
    def __init__(self, pieces):
        self.pieces = pieces
        self.stream_calls = []

    def create(self, stream=False, **kwargs):
        if not stream:  # background summarizer calls
            return NS(choices=[NS(message=NS(content="{}"), finish_reason="stop")], usage=None, model="m")
        self.stream_calls.append(kwargs)
        chunks = [NS(model="vendor/served", usage=None,
                     choices=[NS(delta=NS(content=p), finish_reason=None)]) for p in self.pieces]
        chunks.append(NS(model="vendor/served", usage=None, choices=[NS(delta=NS(content=None), finish_reason="stop")]))
        chunks.append(NS(model="vendor/served", choices=[],
                         usage=NS(prompt_tokens=11, completion_tokens=7, total_tokens=18)))
        self.stream = _FakeStream(chunks)
        return self.stream


def test_chat_stream_yields_deltas_then_saves_sanitized_reply():
    import asyncio
    import backend.db as db
    from backend.memory.memory_store import MemoryStore
    from backend.memory.openrouter_client import OpenRouterClient, _background_tasks
    from backend.models import core

    cid = core.create_conversation("stream")
    client = OpenRouterClient(api_key="test", store=MemoryStore(db_path=db.DB_PATH))
    fake = _FakeCompletions(["Hel", "lo ", "there. <motion>wave</motion>"])
    client._client = NS(chat=NS(completions=fake))

    async def run():
        events = [e async for e in client.chat_stream(cid, "hi there")]
        await asyncio.gather(*list(_background_tasks), return_exceptions=True)
        return events

    events = asyncio.run(run())
    deltas = [e["text"] for e in events if e["type"] == "delta"]
    done = events[-1]
    assert deltas == ["Hel", "lo ", "there. <motion>wave</motion>"]
    assert done["type"] == "done"
    resp = done["response"]
    assert resp.usage["total_tokens"] == 18
    assert resp.model == "vendor/served"
    assert resp.user_message_id and resp.assistant_message_id
    assert fake.stream_calls and fake.stream_calls[0].get("stream_options") == {"include_usage": True}
    assert fake.stream.closed
    saved = [m for m in core.get_messages(cid) if m["role"] == "assistant"]
    assert saved[-1]["content"] == resp.content


def test_chat_stream_endpoint_emits_sse(monkeypatch):
    import backend.api.chat as chat_api
    import backend.app as app_module
    from backend.sarah_core import SarahReply
    from fastapi.testclient import TestClient

    class FakeSarah:
        async def handle_message_stream(self, **kwargs):
            yield {"type": "delta", "text": "Hi "}
            yield {"type": "delta", "text": "Zero"}
            yield {"type": "done", "reply": SarahReply(reply="Hi Zero", user_message_id=1, assistant_message_id=2)}

    monkeypatch.setattr(chat_api, "get_sarah", lambda: FakeSarah())
    monkeypatch.setattr(chat_api, "_ollama_ready", lambda: True)
    monkeypatch.setattr(app_module, "settings", dataclasses.replace(app_module.settings, api_token=""))
    client = TestClient(app_module.create_app())

    resp = client.post("/api/chat/stream", json={"message": "hey", "conversation_id": 1})
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")
    blocks = [b for b in resp.text.split("\n\n") if b.strip()]
    kinds = [b.split("\n")[0] for b in blocks]
    assert kinds == ["event: delta", "event: delta", "event: done"]
    import json as _json
    done = _json.loads(blocks[-1].split("data: ", 1)[1])
    assert done["reply"] == "Hi Zero" and done["assistant_message_id"] == 2


# ---------------------------------------------------------------------------
# Long-term memory + full-text search
# ---------------------------------------------------------------------------

def test_message_search_uses_fts_and_tracks_edits():
    from backend.models import core

    cid = core.create_conversation("fts")
    mid = core.add_message(cid, "user", "My telescope is a Dobsonian, pointed at Jupiter.")
    core.add_message(cid, "assistant", "Nice, enjoy the view!")

    hits = [r["id"] for r in core.search_messages("dobsonian jupiter", 20)]
    assert mid in hits
    assert mid in [r["id"] for r in core.search_messages("telesc", 20)], "prefix match"
    assert mid not in [r["id"] for r in core.search_messages("dobsonian nebula", 20)], "all terms required"

    core.update_message(mid, "Actually it is a refractor now.")
    assert mid not in [r["id"] for r in core.search_messages("dobsonian", 20)]
    assert mid in [r["id"] for r in core.search_messages("refractor", 20)]
    core.delete_message(mid)
    assert mid not in [r["id"] for r in core.search_messages("refractor", 20)]
    # Odd punctuation must not raise (terms are quoted for FTS).
    core.search_messages('"unbalanced (quote* OR', 5)


def test_auto_memories_dedupe_find_and_delete():
    from backend.models import core

    added = core.add_auto_memories([
        "The user keeps a pet axolotl named Pickle.",
        "The user keeps a pet axolotl named Pickle!",   # near-duplicate
        "x",                                               # too short
        "The user's favorite editor is Neovim with the Lazy plugin manager.",
    ])
    assert added == 2
    relevant = core.find_relevant_memories("What should I feed my axolotl?", limit=3)
    assert relevant and "axolotl" in relevant[0]["content"]
    assert core.delete_memory(relevant[0]["id"]) is True
    assert core.delete_memory(relevant[0]["id"]) is False
    assert not [m for m in core.find_relevant_memories("axolotl pickle") if "Pickle" in m["content"]]


def test_rolling_summary_extracts_durable_facts(memory_store):
    import asyncio
    import json as _json
    import sqlite3
    from backend.memory.summarizer import Summarizer
    from backend.models import core

    store, config = memory_store
    conn = sqlite3.connect(store.db_path)
    for i in range(6):  # 3 exchanges -> early first summary
        conn.execute("INSERT INTO messages (conversation_id, role, content) VALUES (1, ?, ?)",
                     ("user" if i % 2 == 0 else "assistant", f"msg {i}"))
    conn.commit()
    conn.close()

    async def fake_llm(prompt, max_tokens):
        assert "durable_facts" in prompt
        return _json.dumps({"goal": "plan a trip", "durable_facts": ["The user lives near the Bitterroot Mountains."]})

    summ = Summarizer(llm_call_fn=fake_llm, store=store, config=config)
    assert asyncio.run(summ.should_update_rolling_summary(1)) is True
    assert asyncio.run(summ.update_rolling_summary(1)) is not None
    assert any("Bitterroot" in m["content"] for m in core.find_relevant_memories("bitterroot mountains"))


def test_context_includes_relevant_long_term_memory(builder):
    from backend.models import core

    core.add_auto_memories(["The user is allergic to hazelnuts."])
    ctx, _ = builder
    packet = ctx.build(1, "Can you suggest a dessert without hazelnuts?", process_mood=False)
    system = packet.messages[0]["content"]
    assert "Long-term memory" in system and "allergic to hazelnuts" in system
    assert packet.debug_info["long_term_memories"] >= 1


@pytest.fixture
def builder(tmp_path):
    import sqlite3
    from backend.memory.config import MemoryConfig
    from backend.memory.context_builder import ContextBuilder
    from backend.memory.memory_store import MemoryStore

    db = tmp_path / "ctx.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE conversations (id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT)")
    conn.execute(
        "CREATE TABLE messages (id INTEGER PRIMARY KEY AUTOINCREMENT, conversation_id INTEGER,"
        " role TEXT, content TEXT, meta_json TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP)"
    )
    conn.execute("INSERT INTO conversations (title) VALUES ('t')")
    conn.commit()
    conn.close()
    config = MemoryConfig(total_token_budget=20000, llm_max_completion_tokens=1000, debug_memory=False)
    return ContextBuilder(store=MemoryStore(db_path=db, config=config), config=config), db


def test_memory_delete_endpoint(monkeypatch):
    import backend.app as app_module
    from backend.models import core
    from fastapi.testclient import TestClient

    monkeypatch.setattr(app_module, "settings", dataclasses.replace(app_module.settings, api_token=""))
    client = TestClient(app_module.create_app())
    core.add_auto_memories(["The user collects vintage fountain pens."])
    mem = core.find_relevant_memories("fountain pens")[0]
    assert client.delete(f"/api/memories/{mem['id']}").status_code == 200
    assert client.delete(f"/api/memories/{mem['id']}").status_code == 404


# ---------------------------------------------------------------------------
# Graceful shutdown
# ---------------------------------------------------------------------------

def test_shutdown_endpoint_requires_token_mode_and_calls_handler(monkeypatch):
    import backend.app as app_module
    import backend.config as config_module
    from backend import lifecycle
    from fastapi.testclient import TestClient

    calls = []
    monkeypatch.setattr(lifecycle, "_shutdown_handler", lambda: calls.append(1))

    # No token configured: refused (any local page could otherwise stop it).
    monkeypatch.setattr(app_module, "settings", dataclasses.replace(app_module.settings, api_token=""))
    monkeypatch.setattr(config_module, "settings", dataclasses.replace(config_module.settings, api_token=""))
    assert TestClient(app_module.create_app()).post("/api/shutdown").status_code == 403
    assert calls == []

    token_settings = dataclasses.replace(config_module.settings, api_token="tok")
    monkeypatch.setattr(app_module, "settings", token_settings)
    monkeypatch.setattr(config_module, "settings", token_settings)
    client = TestClient(app_module.create_app())
    assert client.post("/api/shutdown").status_code == 401
    assert client.post("/api/shutdown", headers={"X-Sarah-Token": "tok"}).status_code == 200
    assert calls == [1]


# ---------------------------------------------------------------------------
# Vision falls back to OpenRouter without Ollama
# ---------------------------------------------------------------------------

def _png_bytes():
    import io
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (8, 8), "white").save(buf, "PNG")
    return buf.getvalue()


def _vision_manager(monkeypatch, *, cloud: bool):
    import backend.services.vision as vision_mod

    monkeypatch.setattr(vision_mod, "_settings", dataclasses.replace(
        vision_mod._settings, openrouter_api_key="k" if cloud else ""))
    # Port 9 (discard) refuses connections: Ollama is "not running".
    return vision_mod.VisionManager(vision_mod.VisionConfig(ollama_url="http://127.0.0.1:9"))


def test_vision_health_reports_cloud_fallback(monkeypatch):
    import asyncio

    vm = _vision_manager(monkeypatch, cloud=True)
    health = asyncio.run(vm.health_check())
    assert health["vision_ready"] is True and health["provider"] == "openrouter"
    assert health["ollama_reachable"] is False and health["fallback_reason"]

    vm_off = _vision_manager(monkeypatch, cloud=False)
    health_off = asyncio.run(vm_off.health_check())
    assert health_off["vision_ready"] is False and health_off["provider"] is None


def test_vision_analyze_routes_to_openrouter_when_ollama_down(monkeypatch):
    import asyncio

    vm = _vision_manager(monkeypatch, cloud=True)
    seen = {}

    async def fake_cloud(processed_bytes, prompt):
        seen["prompt"] = prompt
        return {"ok": True, "provider": "openrouter", "model": "vendor/vision", "analysis": "A white square."}

    monkeypatch.setattr(vm, "_analyze_openrouter", fake_cloud)
    result = asyncio.run(vm.analyze(_png_bytes(), mode="ocr"))
    asyncio.run(vm.close())
    assert result["ok"] and result["provider"] == "openrouter"
    assert result["analysis"] == "A white square." and result["mode"] == "ocr"
    assert "Extract ALL visible text" in seen["prompt"]


def test_vision_analyze_without_any_backend_explains(monkeypatch):
    import asyncio

    vm = _vision_manager(monkeypatch, cloud=False)
    result = asyncio.run(vm.analyze(_png_bytes()))
    asyncio.run(vm.close())
    assert result["ok"] is False and "No vision backend" in result["error"]


def test_vision_body_has_token_floor_and_vision_model(models_env):
    body = llm_models.vision_request_body([{"role": "user", "content": "x"}], max_tokens=96)
    assert body["model"] == "vendor/fallback-a"
    assert body["max_tokens"] >= llm_models.VISION_MIN_COMPLETION_TOKENS
