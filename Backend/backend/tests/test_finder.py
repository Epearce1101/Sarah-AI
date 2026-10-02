"""Finding things on Zero's PC by name (apps, files, folders, projects)."""
from __future__ import annotations

import asyncio

import pytest

from backend.agency import finder, tools


def test_rank_prefers_exact_then_prefix_then_all_words():
    assert finder.rank("Resume.pdf", "my resume") == 100
    assert finder.rank("Resume 2026 final.docx", "resume") == 85
    assert 55 <= finder.rank("Zero - Tax papers 2025", "tax papers") < 85
    assert finder.rank("Steam.lnk", "steam") == 100
    assert finder.rank("Steamworks SDK", "steam") == 85
    assert finder.rank("Holiday photos", "resume") == 0


def test_pick_only_when_clearly_best():
    items = [{"name": "Resume.pdf", "score": 100}, {"name": "Resume old.pdf", "score": 85}]
    assert finder.pick(items)["name"] == "Resume.pdf"
    tie = [{"name": "Budget 2025.xlsx", "score": 85}, {"name": "Budget 2024.xlsx", "score": 85}]
    assert finder.pick(tie) is None
    assert finder.pick([{"name": "x", "score": 45}]) is None
    assert finder.pick([]) is None


def test_walk_finds_files_and_folders_skipping_junk(tmp_path):
    (tmp_path / "Documents" / "Taxes 2025").mkdir(parents=True)
    (tmp_path / "Documents" / "Taxes 2025" / "receipt.pdf").write_text("x")
    (tmp_path / "Documents" / "node_modules" / "taxes").mkdir(parents=True)
    (tmp_path / "Desktop").mkdir()
    (tmp_path / "Desktop" / "Trip plan.pdf").write_text("x")
    folders = finder._walk(["taxes"], "folder", roots=[tmp_path])
    assert [h["name"] for h in folders] == ["Taxes 2025"]
    files = finder._walk(["trip", "plan"], "file", roots=[tmp_path])
    assert files[0]["path"].endswith("Trip plan.pdf")


def test_find_ranks_across_sources(monkeypatch, tmp_path):
    monkeypatch.setattr(finder, "apps", lambda max_age=600: [finder._item("Spotify", "app", "SpotifyAB.Spotify!App", "appid"),
                                                             finder._item("Steam", "app", r"C:\x\Steam.lnk")])
    monkeypatch.setattr(finder, "projects", lambda: [finder._item("Sarah-AI", "project", str(tmp_path))])
    monkeypatch.setattr(finder, "_search_index", lambda words, kind, limit=60: [
        finder._item("Sarah notes.txt", "file", r"C:\Users\Zero\Documents\Sarah notes.txt")])
    assert finder.find("spotify")[0]["how"] == "appid"
    top = finder.find("sarah ai")
    assert top[0]["kind"] == "project"
    assert finder.find("nothing like this") == []


def test_open_item_lists_choices_instead_of_guessing(monkeypatch):
    monkeypatch.setattr(tools, "_app_path", lambda name: None)
    monkeypatch.setattr(finder, "find", lambda q, kind="any", limit=6: [
        {"name": "Budget 2025.xlsx", "kind": "file", "path": "C:/a/Budget 2025.xlsx", "how": "path", "score": 85},
        {"name": "Budget 2024.xlsx", "kind": "file", "path": "C:/a/Budget 2024.xlsx", "how": "path", "score": 85},
    ])
    started = []
    monkeypatch.setattr(finder, "start", lambda item: started.append(item))
    out = asyncio.run(tools.call("open_item", {"target": "budget"}))
    assert out["ok"] and "several things match" in out["result"] and not started


def test_open_item_opens_the_clear_match_and_admits_when_nothing_matches(monkeypatch):
    monkeypatch.setattr(tools, "_app_path", lambda name: None)
    started = []
    monkeypatch.setattr(finder, "start", lambda item: started.append(item))
    monkeypatch.setattr(finder, "find", lambda q, kind="any", limit=6: [
        {"name": "Spotify", "kind": "app", "path": "SpotifyAB.Spotify!App", "how": "appid", "score": 104}])
    out = asyncio.run(tools.call("open_item", {"target": "spotify"}))
    assert out["ok"] and "Opened Spotify" in out["result"] and started
    monkeypatch.setattr(finder, "find", lambda q, kind="any", limit=6: [])
    missing = asyncio.run(tools.call("open_item", {"target": "zzz no such thing"}))
    assert not missing["ok"] and "Couldn't find anything" in missing["result"]
