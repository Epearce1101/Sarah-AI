# tests/test_v11_api.py
"""End-to-end tests of the V11 HTTP endpoints on a real FastAPI app."""
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

pytest.importorskip("dbos")

from backend import db  # noqa: E402


class FakeSarah:
    def __init__(self):
        self.prompts = []

    async def _call_llm(self, prompt, max_tokens=512):
        self.prompts.append(prompt)
        if "background task" in prompt:
            return "STEPS:\n1. Look it up\n2. Write it down\nRESULT:\nHere you go."
        return "Fake answer."


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    data_dir = tmp_path_factory.mktemp("api")
    old = (db.DATA_DIR, db.DB_PATH)
    db.DATA_DIR, db.DB_PATH = data_dir, data_dir / "test.db"
    db.init_db()

    from backend import v11_features
    sarah = FakeSarah()
    app = FastAPI()
    v11_features.setup(app, lambda: sarah)
    with TestClient(app) as c:
        c.sarah = sarah
        c.data_dir = data_dir
        yield c
    db.DATA_DIR, db.DB_PATH = old


def test_status(client):
    s = client.get("/api/v11/status").json()
    assert s["durable_tasks"] is True, s
    assert s["procedure_memory"] and s["proactive"] and s["screen_timeline"]


def test_durable_task_over_http(client):
    r = client.post("/api/durable_tasks", json={"title": "Find a pasta recipe"})
    assert r.status_code == 200, r.text
    wf = r.json()["workflow_id"]

    for _ in range(100):
        info = client.get(f"/api/durable_tasks/{wf}").json()
        if info["status"] == "SUCCESS":
            break
        time.sleep(0.1)
    assert info["output"]["result"] == "Here you go."

    notes = client.get("/api/notifications").json()["notifications"]
    note = next(n for n in notes if n["workflow_id"] == wf)
    assert client.post(f"/api/notifications/{note['id']}/read").json() == {"ok": True}
    assert client.post("/api/notifications/99999/read").status_code == 404

    assert any(t["workflow_id"] == wf for t in client.get("/api/durable_tasks").json()["tasks"])
    assert client.post(f"/api/durable_tasks/{wf}/feedback", json={"good": True}).json()["learned"]

    procs = client.get("/api/procedures/search", params={"task": "Find a pasta recipe"}).json()
    assert procs["procedures"][0]["steps"] == ["Look it up", "Write it down"]


def test_durable_approval_over_http(client):
    wf = client.post(
        "/api/durable_tasks", json={"title": "Empty recycle bin", "wait_for_approval": True}
    ).json()["workflow_id"]
    for _ in range(100):
        if client.get(f"/api/durable_tasks/{wf}").json()["state"] == "waiting_for_approval":
            break
        time.sleep(0.1)
    assert client.post(f"/api/durable_tasks/{wf}/approve", json={"approve": True}).json() == {"ok": True}
    for _ in range(100):
        info = client.get(f"/api/durable_tasks/{wf}").json()
        if info["status"] == "SUCCESS":
            break
        time.sleep(0.1)
    assert info["output"]["cancelled"] is False
    # approving again after it finished is refused, not silently "ok"
    assert client.post(f"/api/durable_tasks/{wf}/approve", json={"approve": True}).status_code == 409


def test_durable_validation_and_404(client):
    assert client.post("/api/durable_tasks", json={"title": "   "}).status_code == 400
    assert client.post("/api/durable_tasks", json={"title": "x", "delay_seconds": -1}).status_code == 400
    assert client.get("/api/durable_tasks/nope").status_code == 404
    assert client.post("/api/durable_tasks/nope/approve", json={"approve": True}).status_code == 404
    assert client.post("/api/durable_tasks/nope/feedback", json={"good": True}).status_code == 404


def test_procedure_endpoints(client):
    assert client.post("/api/procedures/99999/feedback", json={"good": True}).status_code == 404
    assert "procedures" in client.get("/api/procedures").json()


def test_proactive_endpoints(client):
    r = client.post("/api/proactive/check", json={"category": "coding", "text": "Want help with that bug?"})
    body = r.json()
    assert body["offer"] is True and body["suggestion_id"]
    sid = body["suggestion_id"]

    assert client.post(f"/api/proactive/{sid}/feedback", json={"outcome": "accepted"}).json() == {"ok": True}
    assert client.post(f"/api/proactive/{sid}/feedback", json={"outcome": "meh"}).status_code == 400
    assert client.post("/api/proactive/99999/feedback", json={"outcome": "accepted"}).status_code == 404
    assert client.post("/api/proactive/check", json={"category": "", "text": "x"}).status_code == 400
    assert client.get("/api/proactive/stats").json()["coding"]["accepted"] == 1


def test_screen_hook_and_timeline(client):
    from backend import v11_features

    extra = v11_features.on_screen_analysis("Editing server.py in VS Code", None)
    assert extra == {"proactive_offer": False, "suggestion_id": None}
    events = client.get("/api/screen/timeline").json()["events"]
    assert events[-1]["description"] == "Editing server.py in VS Code"

    summary = client.post("/api/screen/timeline/summary").json()["summary"]
    assert summary == "Fake answer."
    assert "Editing server.py in VS Code" in client.sarah.prompts[-1]

    assert client.delete("/api/screen/timeline").json() == {"ok": True}
    assert client.get("/api/screen/timeline").json()["events"] == []
    empty = client.post("/api/screen/timeline/summary").json()["summary"]
    assert empty == "I haven't seen anything on screen yet."


def test_screen_hook_ignores_repeat_polls_of_same_screenshot(client, monkeypatch):
    from backend import v11_features, proactive_engine

    calls = []
    monkeypatch.setattr(proactive_engine, "maybe_offer",
                        lambda *a, **k: calls.append(a) or {"offer": True, "suggestion_id": 7, "reason": "ok"})
    client.delete("/api/screen/timeline")
    first = v11_features.on_screen_analysis("Watching a cooking video", "Save this recipe?", frame="frame-A")
    again = v11_features.on_screen_analysis("Watching a cooking video", "Save this recipe?", frame="frame-A")
    assert first == {"proactive_offer": True, "suggestion_id": 7}
    assert again == {"proactive_offer": False, "suggestion_id": None}
    assert len(calls) == 1  # the repeat poll never reached the proactive engine
    events = client.get("/api/screen/timeline").json()["events"]
    assert len(events) == 1 and events[0]["repeat_count"] == 1
    v11_features.on_screen_analysis("Watching a cooking video", None, frame="frame-B")
    assert client.get("/api/screen/timeline").json()["events"][0]["repeat_count"] == 2


def test_screen_hook_never_raises(client, monkeypatch):
    from backend import v11_features, screen_timeline

    def boom(*a, **k):
        raise RuntimeError("disk full")

    monkeypatch.setattr(screen_timeline, "add_event", boom)
    assert v11_features.on_screen_analysis("x", "y") == {"proactive_offer": False, "suggestion_id": None}


def test_documents_redirect_to_local_network_is_blocked(client, monkeypatch):
    import requests
    from backend import document_reader

    class FakeResp:
        def __init__(self, status, headers, body=b""):
            self.status_code, self.headers, self._body = status, headers, body
            self.is_redirect = status in (301, 302, 303, 307, 308)
        def raise_for_status(self): pass
        def iter_content(self, n): yield self._body
        def close(self): pass

    pages = {
        "https://public.example/doc": FakeResp(302, {"location": "http://192.168.1.1/admin"}),
        "https://public.example/notes.txt": FakeResp(200, {"content-type": "text/plain"}, b"hello from the web"),
    }
    fetched = []
    monkeypatch.setattr(document_reader, "_is_public_url", lambda u: "192.168." not in u)
    monkeypatch.setattr(requests, "get", lambda u, **k: fetched.append(u) or pages[u])

    blocked = document_reader.read_document("https://public.example/doc")
    assert blocked["ok"] is False and "public" in blocked["error"]
    assert fetched == ["https://public.example/doc"]  # the private address was never fetched

    ok = document_reader.read_document("https://public.example/notes.txt")
    assert ok["ok"] and ok["markdown"] == "hello from the web"


def test_documents_plain_text(client):
    doc = client.data_dir / "notes.txt"
    doc.write_text("The meeting is on Friday at 3pm.", encoding="utf-8")

    r = client.post("/api/documents/read", json={"source": str(doc), "max_chars": 11}).json()
    assert r["ok"] and r["markdown"] == "The meeting" and r["truncated"] is True

    a = client.post("/api/documents/ask", json={"source": str(doc), "question": "When is the meeting?"}).json()
    assert a["ok"] and a["answer"] == "Fake answer."
    assert "Friday at 3pm" in client.sarah.prompts[-1]

    local = client.post("/api/documents/read", json={"source": "http://127.0.0.1:8907/api/health"}).json()
    assert local["ok"] is False and "public" in local["error"]

    missing = client.post("/api/documents/read", json={"source": str(client.data_dir / "nope.pdf")}).json()
    assert missing["ok"] is False and "not found" in missing["error"]
    assert client.post("/api/documents/ask", json={"source": str(doc), "question": " "}).status_code == 400
