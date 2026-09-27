"""Tests for local/Ollama LLM routing."""

import asyncio
import sqlite3
from pathlib import Path

import pytest

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from backend.memory.config import MemoryConfig, reset_memory_config
from backend.memory.memory_store import MemoryStore, reset_memory_store
from backend.memory.openrouter_client import OpenRouterClient
from backend.sarah_core import LLMClient


class _FakeResponse:
    def __init__(self, data):
        self._data = data

    def raise_for_status(self):
        return None

    def json(self):
        return self._data


class _FakeSummarizer:
    async def update_all(self, conversation_id: int, assistant_message: str):
        return None


@pytest.fixture
def temp_db(tmp_path):
    db_path = tmp_path / "sarah-test.db"
    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE conversations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute("""
        CREATE TABLE messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            conversation_id INTEGER,
            role TEXT,
            content TEXT,
            meta_json TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute("INSERT INTO conversations (title) VALUES ('Test')")
    conn.commit()
    conn.close()

    yield db_path

    reset_memory_config()
    reset_memory_store()


def test_memory_chat_uses_ollama_chat_endpoint_in_local_mode(monkeypatch, temp_db):
    calls = []

    def fake_post(url, json, timeout):
        calls.append({"url": url, "json": json, "timeout": timeout})
        return _FakeResponse({
            "model": "llama3.1",
            "message": {"content": "local memory reply"},
            "prompt_eval_count": 12,
            "eval_count": 4,
            "done_reason": "stop",
        })

    monkeypatch.setattr("backend.memory.openrouter_client.requests.post", fake_post)

    config = MemoryConfig(
        llm_max_completion_tokens=64,
        llm_temperature=0.2,
        debug_memory=False,
    )
    store = MemoryStore(db_path=temp_db, config=config)
    client = OpenRouterClient(api_key="", store=store, config=config)
    client.summarizer = _FakeSummarizer()
    client.set_llm_mode_info("local", "llama3.1", "Ollama")

    response = asyncio.run(client.chat(
        conversation_id=1,
        user_message="hello",
        save_messages=False,
    ))

    assert response.content == "local memory reply"
    assert response.model == "llama3.1"
    assert response.usage["total_tokens"] == 16
    assert response.debug_info["llm_mode"] == "local"
    assert response.debug_info["llm_provider"] == "Ollama"
    assert calls[0]["url"].endswith("/api/chat")
    assert calls[0]["json"]["model"] == "llama3.1"
    assert calls[0]["json"]["stream"] is False
    assert calls[0]["json"]["options"]["num_predict"] == 48
    assert calls[0]["json"]["options"]["num_ctx"] == 4096
    assert calls[0]["json"]["options"]["temperature"] == 0.2
    assert "\nReason" in calls[0]["json"]["options"]["stop"]
    assert client._llm_mode_info["token_budget"] == 3072
    assert client._llm_mode_info["completion_token_budget"] == 48


def test_legacy_local_completion_uses_configured_ollama_generate(monkeypatch):
    calls = []

    def fake_post(url, json, timeout):
        calls.append({"url": url, "json": json, "timeout": timeout})
        return _FakeResponse({"response": "legacy local reply"})

    import requests
    monkeypatch.setattr(requests, "post", fake_post)

    client = LLMClient(
        mode="local",
        online_model="openrouter/auto",
        local_model="llama3.1",
    )

    result = asyncio.run(client.acompletion("say hi", max_tokens=32))

    assert result == "legacy local reply"
    assert calls[0]["url"].endswith("/api/generate")
    assert calls[0]["json"]["model"] == "llama3.1"
    assert calls[0]["json"]["stream"] is False
    assert calls[0]["json"]["options"]["num_predict"] == 32
    assert calls[0]["json"]["options"]["num_ctx"] == 4096
    assert "\nReason" in calls[0]["json"]["options"]["stop"]


def test_legacy_online_without_openrouter_key_falls_back_to_local(monkeypatch):
    calls = []

    def fake_post(url, json, timeout):
        calls.append({"url": url, "json": json, "timeout": timeout})
        return _FakeResponse({"response": "local fallback reply"})

    import requests
    monkeypatch.setattr(requests, "post", fake_post)

    client = LLMClient(
        mode="online",
        online_model="openrouter/auto",
        local_model="jessie:latest",
    )
    client.openrouter_api_key = ""
    client._client = None

    result = asyncio.run(client.acompletion("say hi", max_tokens=32))

    assert result == "local fallback reply"
    assert calls[0]["url"].endswith("/api/generate")
    assert calls[0]["json"]["model"] == "jessie:latest"
    assert calls[0]["json"]["stream"] is False
    assert calls[0]["json"]["options"]["num_predict"] == 32


def test_legacy_online_auth_error_falls_back_to_local(monkeypatch):
    calls = []

    class _FailingCompletions:
        def create(self, **kwargs):
            raise RuntimeError("401 auth failed")

    class _FailingChat:
        completions = _FailingCompletions()

    class _FailingOpenRouterClient:
        chat = _FailingChat()

    def fake_post(url, json, timeout):
        calls.append({"url": url, "json": json, "timeout": timeout})
        return _FakeResponse({"response": "local after auth failure"})

    import requests
    monkeypatch.setattr(requests, "post", fake_post)

    client = LLMClient(
        mode="online",
        online_model="openrouter/auto",
        local_model="jessie:latest",
    )
    client.openrouter_api_key = "bad-key"
    client._client = _FailingOpenRouterClient()

    result = asyncio.run(client.acompletion("say hi", max_tokens=32))

    assert result == "local after auth failure"
    assert calls[0]["url"].endswith("/api/generate")
    assert calls[0]["json"]["model"] == "jessie:latest"
