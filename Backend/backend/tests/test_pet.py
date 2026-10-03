"""Desktop pet: getting out of Zero's way (gap beside a window, another
monitor for fullscreen) and coming back."""
import asyncio

import pytest

from backend.agency import desktop, pet, tools

PRIMARY = {"rect": (0, 0, 1920, 1080), "work": (0, 0, 1920, 1040), "primary": True, "scale": 1.0}
SECOND = {"rect": (1920, 0, 4480, 1440), "work": (1920, 0, 4480, 1400), "primary": False, "scale": 1.0}
SIZE = (170, 290)  # her pet window in DIP


def win(rect, title="Notes - Notepad"):
    return {"title": title, "app": "notepad.exe", "rect": rect}


def pet_at(x, y, w=170, h=290):
    return (x, y, x + w, y + h)


def clear_of(spot, rect):
    return pet.overlap(spot, rect) == 0


def test_a_windowed_app_sends_her_into_the_gap_beside_it():
    app = win((300, 100, 1500, 900))
    out = pet.plan(pet_at(1000, 600), SIZE, [PRIMARY, SECOND], [app], app)
    spot = out["spot"]
    assert out["move"] and "gap beside Notes - Notepad" in out["reason"]
    assert clear_of(spot, app["rect"]) and pet.covers(PRIMARY["work"], spot)  # same screen, off the app
    assert spot[0] >= 1500 and spot[3] == 1040  # the bigger (right-hand) gap, standing on the taskbar


def test_she_picks_the_gap_no_other_window_is_in():
    app = win((300, 100, 1500, 900))
    chat = win((1500, 0, 1920, 1040), "Discord")
    out = pet.plan(pet_at(1000, 600), SIZE, [PRIMARY], [app, chat], app)
    assert out["move"] and out["spot"][2] <= 300  # the left-hand gap
    assert clear_of(out["spot"], app["rect"]) and clear_of(out["spot"], chat["rect"])


def test_a_fullscreen_app_sends_her_to_the_other_monitor():
    game = win((0, 0, 1920, 1080), "Elden Ring")
    out = pet.plan(pet_at(1734, 750), SIZE, [PRIMARY, SECOND], [game], game)
    assert out["move"] and "fullscreen" in out["reason"] and "other monitor" in out["reason"]
    assert pet.covers(SECOND["work"], out["spot"])


def test_fullscreen_on_the_second_monitor_sends_her_to_the_main_one():
    game = win((1920, 0, 4480, 1440), "Elden Ring")
    out = pet.plan(pet_at(3000, 1000), SIZE, [PRIMARY, SECOND], [game], game)
    assert out["move"] and pet.covers(PRIMARY["work"], out["spot"])


def test_already_on_the_other_monitor_she_stays():
    game = win((0, 0, 1920, 1080), "Elden Ring")
    out = pet.plan(pet_at(3000, 1000), SIZE, [PRIMARY, SECOND], [game], game)
    assert not out["move"] and "already on the other screen" in out["reason"]


def test_fullscreen_with_one_monitor_tucks_into_the_corner():
    game = win((0, 0, 1920, 1080), "Elden Ring")
    out = pet.plan(pet_at(800, 400), SIZE, [PRIMARY], [game], game)
    assert out["move"] and "no other monitor" in out["reason"]
    assert out["spot"][2] > 1700 and out["spot"][3] == 1040  # bottom-right


def test_a_maximized_window_leaves_no_gap_so_she_changes_monitor():
    browser = win((0, 0, 1920, 1040), "YouTube - Google Chrome")
    out = pet.plan(pet_at(1734, 750), SIZE, [PRIMARY, SECOND], [browser], browser)
    assert out["move"] and "no gap" in out["reason"] and pet.covers(SECOND["work"], out["spot"])
    alone = pet.plan(pet_at(1734, 750), SIZE, [PRIMARY], [browser], browser)
    assert alone["move"] and "corner" in alone["reason"]


def test_already_clear_of_the_app_she_stays():
    app = win((300, 100, 1200, 700))
    out = pet.plan(pet_at(1700, 750), SIZE, [PRIMARY], [app], app)
    assert not out["move"] and "already clear" in out["reason"]


def test_her_size_follows_display_scaling():
    scaled = {**PRIMARY, "scale": 1.5}
    app = win((300, 100, 1300, 900))
    out = pet.plan(pet_at(1000, 600, 255, 435), SIZE, [scaled], [app], app)
    spot = out["spot"]
    assert (spot[2] - spot[0], spot[3] - spot[1]) == (255, 435)


def test_nothing_open_nothing_to_do():
    assert not pet.plan(pet_at(0, 0), SIZE, [PRIMARY], [], None)["move"]


# --- the tool, with a fake app window and screen --------------------------------------------

@pytest.fixture
def body(monkeypatch):
    from backend.agency import senses as senses_mod

    state = {"pet": True, "home": None, "at": (1000, 600), "requests": []}

    async def request(kind, payload, timeout=30):
        state["requests"].append((kind, payload))
        if not state["pet"]:
            return {"pet": False}
        if kind == "pet_where":
            x, y = state["at"]
            return {"pet": True, "dip": {"width": 170, "height": 290},
                    "physical": {"x": x, "y": y, "width": 170, "height": 290}, "home": bool(state["home"])}
        if payload.get("back"):
            state["at"], state["home"] = state["home"], None
        else:
            state["home"] = state["home"] or state["at"]
            state["at"] = (payload["x"], payload["y"])
        return {"pet": True, "bounds": {"x": state["at"][0], "y": state["at"][1], "width": 170, "height": 290}}

    monkeypatch.setattr(senses_mod.senses, "request", request)
    app = win((300, 100, 1500, 900))
    monkeypatch.setattr(desktop, "screen_layout", lambda: {"monitors": [PRIMARY], "windows": [app], "active": app})
    return state


def run(coro):
    return asyncio.run(coro)


def test_the_tool_moves_her_out_of_the_way_and_back(body):
    out = run(tools.call("pet", {"action": "out_of_the_way"}))
    assert out["ok"] and "gap beside" in out["result"] and "checked" in out["result"]
    assert body["at"][0] >= 1500
    back = run(tools.call("pet", {"action": "back"}))
    assert back["ok"] and "back to where I was" in back["result"] and body["at"] == (1000, 600)
    again = run(tools.call("pet", {"action": "back"}))
    assert again["ok"] and "already where Zero put me" in again["result"]


def test_the_tool_outside_pet_mode_says_so(body):
    body["pet"] = False
    out = run(tools.call("pet", {"action": "out_of_the_way"}))
    assert not out["ok"] and "not in desktop pet mode" in out["result"]
    assert [k for k, _ in body["requests"]] == ["pet_where"]  # nothing was moved
