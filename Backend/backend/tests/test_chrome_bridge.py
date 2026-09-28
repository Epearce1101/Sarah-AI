"""Sarah in Zero's Chrome: the Browser Bridge extension's backend side."""
import asyncio

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from backend.agency import tools
from backend.agency.chrome_bridge import ALLOWED_ORIGIN, ChromeBridge, bridge


def run(coro):
    return asyncio.run(coro)


class FakeExtension:
    """Answers the bridge's requests the way the extension would."""

    def __init__(self, bridge_obj, answers):
        self.bridge = bridge_obj
        self.answers = answers
        self.sent = []

    async def send_json(self, msg):
        self.sent.append(msg)
        answer = self.answers.get(msg["action"])
        reply = {"id": msg["id"], "ok": not isinstance(answer, Exception)}
        if isinstance(answer, Exception):
            reply["error"] = str(answer)
        else:
            reply["result"] = answer
        asyncio.get_running_loop().call_soon(self.bridge.on_message, reply)


def test_requests_and_answers_are_matched():
    b = ChromeBridge()
    ext = FakeExtension(b, {"open": {"tab_id": 7, "title": "Example"}, "click": RuntimeError("nothing matching")})
    b.attach(ext)
    b.on_message({"type": "hello", "version": "1.0.0", "ua": "Chrome"})
    assert b.status()["connected"] and b.status()["version"] == "1.0.0"

    assert run(b.act("open", url="example.com", visible=True))["tab_id"] == 7
    assert ext.sent[0] == {"id": "c1", "action": "open", "args": {"url": "example.com"}}  # visible isn't Chrome's
    with pytest.raises(RuntimeError, match="nothing matching"):
        run(b.act("click", text="Buy"))
    with pytest.raises(ValueError):
        run(b.act("teleport"))
    b.detach(ext)
    assert not b.connected()
    with pytest.raises(ConnectionError):
        run(b.call("tabs"))


def test_browser_tool_uses_chrome_when_connected(monkeypatch):
    from backend.agency import browser as own_browser

    calls = []

    async def chrome_act(action, **kw):
        calls.append(("chrome", action, kw))
        return {"where": "chrome"}

    async def own_act(action, **kw):
        calls.append(("own", action, kw))
        return {"where": "own"}

    monkeypatch.setattr(bridge, "act", chrome_act)
    monkeypatch.setattr(own_browser.browser, "act", own_act)
    monkeypatch.setattr(bridge, "ws", object())
    assert "chrome" in run(tools.call("browser", {"action": "open", "url": "example.com"}))["result"]
    assert "own" in run(tools.call("browser", {"action": "read", "where": "own", "tab_id": 3}))["result"]
    assert calls[-1] == ("own", "read", {})  # tab_id means nothing to her own browser
    monkeypatch.setattr(bridge, "ws", None)
    assert "own" in run(tools.call("browser", {"action": "open", "url": "example.com"}))["result"]
    assert "isn't connected" in run(tools.call("browser", {"action": "tabs"}))["result"]


def test_websites_open_as_a_chrome_tab_when_connected(monkeypatch):
    async def call(action, args=None, timeout=60):
        assert action == "open" and args["url"] == "https://youtube.com"
        return {"tab_id": 12, "title": "YouTube"}

    monkeypatch.setattr(bridge, "ws", object())
    monkeypatch.setattr(bridge, "call", call)
    out = run(tools.call("open_item", {"target": "youtube.com"}))
    assert out["ok"] and "new Chrome tab" in out["result"] and "12" in out["result"]


def test_only_the_extension_may_connect():
    import backend.app as app_module

    client = TestClient(app_module.create_app())
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/ws/chrome", headers={"origin": "https://evil.example"}) as ws:
            ws.receive_json()
    with client.websocket_connect("/ws/chrome", headers={"origin": ALLOWED_ORIGIN}) as ws:
        ws.send_json({"type": "hello", "version": "1.0.0"})
        assert client.get("/api/agency/chrome").json()["connected"] in (True, False)  # endpoint answers
    assert client.get("/api/agency/chrome").json()["folder"].endswith("chrome-extension")
