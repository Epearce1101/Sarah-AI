"""Pet-mode pointing: matching what Zero asked about to what's on screen."""
from __future__ import annotations

from backend.agency.locate import best, keys_for, locate, score


def test_keys_for_paths_names_and_urls():
    assert keys_for(r"C:\Users\Zero\Desktop\Trip plan.pdf") == {"name": "trip plan.pdf", "stem": "trip plan", "path": "1"}
    assert keys_for("Desktop/report.docx")["stem"] == "report"
    assert keys_for("Documents/")["name"] == "documents"
    assert keys_for("Spotify") == {"name": "spotify", "stem": "spotify", "path": ""}
    assert keys_for("https://youtube.com") is None
    assert keys_for("C:\\") is None
    assert keys_for("  ") is None


def test_score_window_titles_icons_and_apps():
    pdf = keys_for("Desktop/Trip plan.pdf")
    assert score("Trip plan", pdf) == 3                     # desktop icon, extension hidden
    assert score("Trip plan.pdf - Adobe Acrobat Reader", pdf) == 2
    assert score("Trip planner notes", pdf) == 0             # not the same file
    chrome = keys_for("chrome")
    assert score("New Tab - Google Chrome", chrome, "chrome.exe") == 2
    assert score("Google Chrome", chrome) == 2               # taskbar button / shortcut
    assert score("Chromecast setup", chrome) == 0
    assert score("", chrome, "notepad.exe") == 0


def test_best_prefers_strong_and_front_most_and_skips_offscreen():
    keys = keys_for("Downloads")
    cands = [
        ("Downloads", "folder window", (-32000, -32000, 160, 28), "explorer.exe"),   # minimised
        ("Downloads", "folder window", (100, 50, 800, 600), "explorer.exe"),
        ("Downloads", "desktop icon", (20, 20, 75, 70), ""),
    ]
    hit = best(cands, keys)
    assert hit["kind"] == "folder window" and (hit["x"], hit["y"]) == (500, 350)
    assert best([("Music", "desktop icon", (0, 0, 10, 10), "")], keys) is None


def test_locate_without_a_target_or_off_windows():
    assert locate("https://example.com")["found"] is False
    assert locate("")["found"] is False
