"""Sarah's sight: free-only models, budget, parsing, and what she's told."""
import time

import pytest

import backend.perception.sight as sight
from backend.embodiment.self_model import get_self, reset_self


class FakeSettings:
    vision_models = ("dots-studio/dots-3-note-preview:free", "openai/gpt-5-vision", "qwen/qwen3.8-27b:free")
    vision_daily_cap = 10
    vision_min_interval_seconds = 10


@pytest.fixture(autouse=True)
def fake_settings(monkeypatch):
    monkeypatch.setattr(sight, "settings", FakeSettings())
    monkeypatch.setattr(sight, "get_user_name", lambda: "Zero")
    reset_self()
    yield
    reset_self()


def test_only_free_models_are_ever_used():
    assert sight.free_vision_models() == ["dots-studio/dots-3-note-preview:free", "qwen/qwen3.8-27b:free"]


def test_budget_spacing_cap_and_backoff():
    b = sight.VisionBudget()
    assert b.check() is None
    assert b.check() == "already looking"
    b.done(ok=True)
    assert b.check() == "too soon"
    assert b.check(urgent=True) is None  # an explicit request may look sooner
    b.done(ok=False, rate_limited=True)
    assert b.check(urgent=True).startswith("rate limited")
    b.backoff_until = 0
    b.used = 10
    assert b.check(urgent=True) == "daily budget used"


def test_spacing_widens_as_budget_runs_down():
    b = sight.VisionBudget()
    assert b._interval() == 10
    b.used = 5
    assert b._interval() == 20
    b.used = 8
    assert b._interval() == 40


def test_prompt_labels_and_parse():
    p = sight._prompt(True, True, "is the test passing?")
    assert "image 1 is Zero's screen" in p and "image 2 is Zero's webcam" in p and '"answer"' in p
    assert "camera" not in sight._prompt(True, False, None).split("JSON only")[1]
    assert sight._parse('```json\n{"screen": {"app": "VS Code"}, "notable": null}\n```')["screen"]["app"] == "VS Code"
    assert sight._parse("no json here")["raw"] == "no json here"


def test_what_she_sees_reaches_the_moment():
    me = get_self()
    me.see({"screen": {"app": "Visual Studio Code", "activity": "debugging", "details": "assert 3 == 4"},
            "camera": {"present": "false", "doing": "not in view"}, "notable": "a failing test"})
    text = me.render_now(1, "Zero")
    assert "On Zero's screen (just now): Visual Studio Code - debugging. assert 3 == 4" in text
    assert "Zero isn't at the desk" in text
    assert "You noticed: a failing test" in text
    me.see({"camera": {"present": True, "doing": "drinking coffee", "mood": "relaxed"}, "notable": "null"})
    text = me.render_now(1, "Zero")
    assert "Zero is drinking coffee, looking relaxed" in text
    assert text.count("You noticed") == 1  # "null" isn't something she noticed
    me.sight["screen"]["at"] = time.time() - 3600
    assert "On Zero's screen" not in me.render_now(1, "Zero")


def test_she_knows_when_her_view_is_dark():
    me = get_self()
    me.update_body({"activity": "idle", "eyes_screen": "dark", "eyes_camera": "dark"})
    text = me.render_now(1, "Zero")
    assert "screen is dark right now (locked" in text and "camera sees only darkness" in text
    me.update_body({"activity": "idle", "eyes_screen": "off", "eyes_camera": "off", "ears": "off"})
    text = me.render_now(1, "Zero")
    assert "eyes are switched off by Zero" in text and "microphone is switched off" in text
    me.update_body({"activity": "idle", "eyes_screen": "on", "eyes_camera": "off"})
    assert "camera is switched off by Zero" in me.render_now(1, "Zero")
