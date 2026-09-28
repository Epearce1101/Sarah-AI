"""Desktop apps: real windows (Notepad) opened, found and closed."""
import asyncio
import subprocess
import time

import pytest

from backend.agency import desktop, tools


def run(coro):
    return asyncio.run(coro)


def test_window_list_hides_sarah_and_shows_apps():
    listed = run(tools.call("window", {"action": "list"}))
    assert listed["ok"]
    assert "Sarah V10" not in listed["result"]


def test_protected_apps_are_never_closed(monkeypatch):
    monkeypatch.setattr(desktop, "find", lambda t: {"hwnd": 1, "title": "File Explorer", "pid": 4, "app": "explorer.exe"})
    with pytest.raises(desktop.DesktopError):
        desktop.close("explorer", save=False)


def test_typing_is_refused_honestly_when_locked(monkeypatch):
    monkeypatch.setattr(desktop, "is_locked", lambda: True)
    out = run(tools.call("control_input", {"action": "type", "text": "hi", "window": "notepad"}))
    assert not out["ok"] and "locked" in out["result"]


def test_open_wait_and_close_notepad():
    """Launches the real Notepad (works even on a locked PC), finds it, closes it."""
    before = {w["hwnd"] for w in desktop.windows() if w["app"] == "notepad.exe"}
    assert run(tools.call("open_item", {"target": "notepad"}))["ok"]
    new = None
    for _ in range(40):
        new = [w for w in desktop.windows() if w["app"] == "notepad.exe" and w["hwnd"] not in before]
        if new:
            break
        time.sleep(0.25)
    if not new:
        pytest.skip("Notepad didn't open a new window (single-window Notepad reused?)")
    hwnd = new[0]["hwnd"]
    out = desktop.close(str(new[0]["title"]), save=None)
    assert "Closed" in out, out
    time.sleep(0.5)
    assert hwnd not in {w["hwnd"] for w in desktop.windows()}
