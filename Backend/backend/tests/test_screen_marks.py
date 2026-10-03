"""Screen overlay: finding what she means on screen and marking it."""
from __future__ import annotations

import asyncio

from backend.agency import locate, tools
from backend.agency.senses import senses


def test_text_score_prefers_the_thing_itself():
    assert locate.text_score("Save changes", "save changes") == 3
    assert locate.text_score("Click Save changes to keep it", "Save changes") == 2
    assert locate.text_score("Changes were saved", "save changes") == 0       # different words
    assert locate.text_score("Error: permission denied (EACCES)", "permission denied") == 2
    assert locate.text_score("Settings and privacy", "privacy settings") == 1  # every word, other order


def test_best_text_picks_exact_then_shortest_then_front_most():
    cands = [
        ("Read about Save changes in the docs", "text in Help", (10, 10, 300, 20), ""),
        ("Save changes", "button in Editor", (500, 400, 120, 30), ""),
        ("Save changes", "button in Other", (900, 400, 120, 30), ""),
        ("Save changes", "button offscreen", (-32000, -32000, 120, 30), ""),
    ]
    hit = locate.best_text(cands, "save changes")
    assert hit["kind"] == "button in Editor" and (hit["x"], hit["y"]) == (560, 415)
    assert locate.best_text([("Help", "text", (0, 0, 10, 10), "")], "save") is None


def _pushed(monkeypatch):
    sent = []

    async def push(message):
        sent.append(message)
        return True

    monkeypatch.setattr(senses, "push", push)
    return sent


def test_show_on_screen_marks_an_exact_find(monkeypatch):
    sent = _pushed(monkeypatch)
    monkeypatch.setattr(locate, "find_on_screen", lambda target: {
        "found": True, "kind": "button in Editor", "label": "Save changes", "x": 560, "y": 415, "rect": [500, 400, 120, 30]})
    out = asyncio.run(tools.call("show_on_screen", {"target": "Save changes", "label": "click this"}))
    assert out["ok"] and "Marked 'Save changes'" in out["result"] and "approximate" not in out["result"]
    assert sent[0]["type"] == "mark" and sent[0]["marks"][0] == {
        "rect": [500, 400, 120, 30], "style": "circle", "label": "click this", "approx": False}


def test_show_on_screen_falls_back_to_sight_and_says_so(monkeypatch):
    sent = _pushed(monkeypatch)
    monkeypatch.setattr(locate, "find_on_screen", lambda target: {"found": False})

    async def ground(target):
        return {"found": True, "kind": "seen in a screenshot", "label": target, "approx": True,
                "x": 100, "y": 100, "rect": [80, 90, 40, 20]}

    monkeypatch.setattr(tools, "_ground_on_screen", ground)
    out = asyncio.run(tools.call("show_on_screen", {"target": "the red boss health bar", "style": "arrow"}))
    assert out["ok"] and "approximate" in out["result"]
    assert sent[0]["marks"][0]["approx"] is True and sent[0]["marks"][0]["style"] == "arrow"


def test_show_on_screen_is_honest_when_it_cannot(monkeypatch):
    _pushed(monkeypatch)
    monkeypatch.setattr(locate, "find_on_screen", lambda target: {"found": False})

    async def nothing(target):
        return {"found": False, "reason": "her screen view is off"}

    monkeypatch.setattr(tools, "_ground_on_screen", nothing)
    out = asyncio.run(tools.call("show_on_screen", {"target": "zzz"}))
    assert not out["ok"] and "screen view is off" in out["result"]

    async def offline(message):
        return False

    monkeypatch.setattr(senses, "push", offline)
    monkeypatch.setattr(locate, "find_on_screen", lambda target: {
        "found": True, "kind": "text", "label": "x", "x": 1, "y": 1, "rect": [0, 0, 2, 2]})
    out = asyncio.run(tools.call("show_on_screen", {"target": "x"}))
    assert not out["ok"] and "isn't connected" in out["result"]
