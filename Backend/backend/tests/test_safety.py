"""Zero's OK before deleting/moving, and the stop after repeated failures."""
import asyncio
import re
import time
from types import SimpleNamespace

import pytest

from backend.agency import desktop, safety, tools


def run(coro):
    return asyncio.run(coro)


def _approval_id(result: str) -> str:
    return re.search(r'approval_id="([0-9a-f]+)"', result).group(1)


# --- asking before deleting or moving -------------------------------------------------

@pytest.mark.parametrize("text, expected", [
    ("yes", True), ("Yeah go ahead", True), ("sure, delete it", True), ("ok", True), ("do it", True),
    ("no", False), ("don't, I still need it", False), ("wait", False), ("nah keep it", False),
    ("what's in it?", None), ("hmm", None),
])
def test_reads_zeros_answer(text, expected):
    assert safety.reads_as_yes(text) is expected


def test_delete_asks_first_and_runs_only_after_zero_says_yes(tmp_path):
    target = tmp_path / "old.txt"
    target.write_text("x")

    first = run(tools.call("delete_path", {"path": str(target)}))
    assert not first["ok"] and "NOT DONE YET" in first["result"] and target.exists()
    pid = _approval_id(first["result"])

    # She can't approve it herself: Zero hasn't said anything yet.
    early = run(tools.call("delete_path", {"path": str(target), "approval_id": pid}))
    assert not early["ok"] and "hasn't answered" in early["result"] and target.exists()

    safety.note_user_message("yes go ahead")
    done = run(tools.call("delete_path", {"path": str(target), "approval_id": pid}))
    assert done["ok"] and "Recycle Bin" in done["result"] and not target.exists()

    # Used once: the same id doesn't delete something else later.
    other = tmp_path / "other.txt"
    other.write_text("y")
    again = run(tools.call("delete_path", {"path": str(other), "approval_id": pid}))
    assert not again["ok"] and other.exists()
    assert safety.tripped() is None  # waiting on Zero never counts as failing


def test_no_means_no(tmp_path):
    target = tmp_path / "keep.txt"
    target.write_text("x")
    pid = _approval_id(run(tools.call("delete_path", {"path": str(target)}))["result"])
    safety.note_user_message("no, I still need that")
    out = run(tools.call("delete_path", {"path": str(target), "approval_id": pid}))
    assert not out["ok"] and "said no" in out["result"] and target.exists()


def test_an_unclear_reply_is_asked_again(tmp_path):
    target = tmp_path / "maybe.txt"
    target.write_text("x")
    pid = _approval_id(run(tools.call("delete_path", {"path": str(target)}))["result"])
    safety.note_user_message("what's in it?")
    out = run(tools.call("delete_path", {"path": str(target), "approval_id": pid}))
    assert not out["ok"] and "wasn't a clear yes" in out["result"] and target.exists()
    safety.note_user_message("ok fine")
    assert run(tools.call("delete_path", {"path": str(target), "approval_id": pid}))["ok"]


def test_an_approval_is_for_that_exact_action(tmp_path):
    a, b = tmp_path / "a.txt", tmp_path / "b.txt"
    a.write_text("a")
    b.write_text("b")
    pid = _approval_id(run(tools.call("delete_path", {"path": str(a)}))["result"])
    safety.note_user_message("yes")
    out = run(tools.call("delete_path", {"path": str(b), "approval_id": pid}))
    assert not out["ok"] and "NOT DONE YET" in out["result"] and b.exists()


def test_move_asks_first(tmp_path):
    src, dst = tmp_path / "report.txt", tmp_path / "archive" / "report.txt"
    src.write_text("r")
    dst.parent.mkdir()
    first = run(tools.call("move_path", {"source": str(src), "destination": str(dst)}))
    assert not first["ok"] and "NOT DONE YET" in first["result"] and src.exists()
    safety.note_user_message("yep")
    done = run(tools.call("move_path", {"source": str(src), "destination": str(dst),
                                        "approval_id": _approval_id(first["result"])}))
    assert done["ok"] and dst.exists() and not src.exists()


def test_her_own_scratch_files_need_no_ok():
    assert run(tools.call("write_file", {"path": "notes/scratch_safety.txt", "content": "hi"}))["ok"]
    out = run(tools.call("delete_path", {"path": "notes/scratch_safety.txt"}))
    assert out["ok"] and "Recycle Bin" in out["result"]


@pytest.mark.parametrize("command, asks", [
    ("Remove-Item $HOME\\Downloads\\a.pdf", True),
    ("Move-Item $HOME\\Downloads\\a.pdf $HOME\\Documents\\", True),
    ("del notes.txt", True),
    ("Rename-Item a.txt b.txt", True),
    ("[System.IO.File]::Delete('C:\\Users\\Zero\\a.txt')", True),
    ("Get-ChildItem $HOME\\Downloads", False),
    ("Get-Process | Sort-Object CPU -Descending", False),
    ("python --version", False),
])
def test_shell_commands_that_delete_or_move(command, asks):
    assert safety.shell_deletes_or_moves(command) is asks


def test_run_shell_asks_before_deleting():
    out = run(tools.call("run_shell", {"command": "Remove-Item $HOME\\Downloads\\a.pdf"}))
    assert not out["ok"] and "NOT DONE YET" in out["result"]


def test_her_python_is_told_to_ask_too():
    tools._write_guard_hook()
    code = (tools.WORKSPACE / "sarah_guard_hook.py").read_text(encoding="utf-8")
    compile(code, "sarah_guard_hook.py", "exec")
    assert "_FREE" in code and "needs Zero's OK" in code


# --- stopping after repeated failures ---------------------------------------------------

@pytest.fixture
def screen(monkeypatch):
    monkeypatch.setattr(safety, "_snapshot", lambda: r"C:\sarah\snapshots\stopped.png")
    monkeypatch.setattr(safety, "_error_windows", lambda: ["Windows cannot find 'spotfy'"])


def _fail():
    return run(tools.call("read_file", {"path": "no/such/file_for_safety_test.txt"}))


def test_five_failures_stop_her_with_a_reason_and_a_screenshot(screen):
    for _ in range(4):
        out = _fail()
        assert not out["ok"] and "STOPPED" not in out["result"]
    fifth = _fail()
    assert "STOPPED" in fifth["result"]
    assert "stopped.png" in fifth["result"] and "Windows cannot find" in fifth["result"]
    halt = safety.tripped()
    assert halt["snapshot"].endswith("stopped.png") and len(halt["failures"]) == 5

    # Every tool is refused now, even ones that would work.
    refused = run(tools.call("list_my_tools", {}))
    assert not refused["ok"] and "paused" in refused["result"]

    # Zero saying anything lifts it.
    safety.note_user_message("what happened?")
    assert safety.tripped() is None
    assert run(tools.call("list_my_tools", {}))["ok"]


def test_resume_button_lifts_the_stop(screen):
    for _ in range(5):
        _fail()
    assert safety.tripped()
    tools.resume()
    assert safety.tripped() is None


def test_old_failures_dont_count(screen):
    old = time.time() - 16 * 60
    for _ in range(4):
        safety._failures.append((old, "read_file", "gone"))
    assert "STOPPED" not in _fail()["result"]
    assert safety.tripped() is None


def test_a_made_up_tool_counts_as_a_failure(screen):
    for _ in range(4):
        run(tools.call("teleport", {}))
    assert "STOPPED" in run(tools.call("teleport", {}))["result"]


def test_a_stuck_turn_ends_and_she_says_why(monkeypatch, screen):
    from backend.memory import openrouter_client as oc
    from backend.tests.test_agency import FakeStream, _chunk, _tc

    offered, notes = [], []

    class Completions:
        def create(self, **kw):
            offered.append(bool(kw.get("tools")))
            notes.extend(m["content"] for m in kw["messages"] if m["role"] == "system")
            if kw.get("tools"):  # keeps trying the same broken thing
                return FakeStream([_chunk(tool_calls=[_tc(0, f"c{len(offered)}", "read_file",
                                                          '{"path": "no/such/file.txt"}')], finish="tool_calls")])
            return FakeStream([_chunk("I stopped: that file isn't there.", finish="stop")])

    client = oc.OpenRouterClient.__new__(oc.OpenRouterClient)
    client._client = SimpleNamespace(chat=SimpleNamespace(completions=Completions()))
    client.config = SimpleNamespace(llm_max_completion_tokens=100, llm_temperature=0.5, chars_per_token=4)
    client._is_local_mode = lambda: False
    packet = SimpleNamespace(messages=[{"role": "user", "content": "open it"}], estimated_tokens=10, debug_info={})
    client._prepare_turn = lambda *a, **k: (packet, 1)
    client._finish_turn = lambda **kw: SimpleNamespace(content=kw["raw_content"])
    monkeypatch.setattr(oc.llm_models, "completion_kwargs", lambda **k: {"model": "fake:free", "extra_body": None})
    monkeypatch.setattr(tools, "_custom_tools", lambda: {})

    async def collect():
        return [e async for e in client.chat_stream(1, "open it")]

    events = run(collect())
    assert offered == [True] * 5 + [False]  # five failures, then one answer without tools
    assert any("STOPPED" in n for n in notes)
    assert events[-1]["response"].content == "I stopped: that file isn't there."


def test_her_mind_waits_and_never_goes_quiet_about_stopping(monkeypatch, screen):
    import backend.models.core as core
    import backend.state as state_mod
    from backend.agency import mind as mind_mod, senses as senses_mod
    from backend.embodiment.self_model import get_self, reset_self

    reset_self()
    me = get_self()
    me.last_conversation_id = 3
    m = mind_mod.Mind()
    pushed = []

    class Client:
        async def chat_stream(self, **kw):
            for _ in range(5):
                await tools.call("read_file", {"path": "no/such/file.txt"})
            yield {"type": "done", "response": SimpleNamespace(finish_reason="stop", content="<silent/>")}

    sarah = SimpleNamespace(_openrouter=Client(), _derive_emotion=lambda cid: ("worried", 0.4))
    monkeypatch.setattr(state_mod, "get_sarah", lambda: sarah)
    monkeypatch.setattr(core, "add_message", lambda *a, **k: 12)

    async def push(msg):
        pushed.append(msg)
        return True

    monkeypatch.setattr(senses_mod.senses, "push", push)
    try:
        record = run(m.think(["agenda #1 is due: open the report"]))
        assert record["outcome"] == "spoke" and "failed" in record["stopped"]
        said = pushed[-1]["reply"]["reply"]
        assert said.startswith("I stopped what I was doing because") and "stopped.png" in said
        assert m.ready(time.time() + 3600) == "stopped after repeated failures (waiting for Zero)"
    finally:
        reset_self()


# --- opening apps without error boxes ------------------------------------------------------

def test_an_unknown_app_is_not_started(monkeypatch):
    monkeypatch.setattr(desktop, "_start_menu_shortcuts", lambda: [])
    monkeypatch.setattr(desktop, "_store_apps", lambda: [])
    monkeypatch.setattr(desktop.shutil, "which", lambda name: None)
    assert desktop.find_app("spotfy") is None
    out = run(tools.call("open_item", {"target": "spotfy"}))
    assert not out["ok"] and "couldn't find" in out["result"]


def test_finding_apps(monkeypatch, tmp_path):
    monkeypatch.setattr(desktop.shutil, "which", lambda name: None)
    lnk = tmp_path / "Spotify.lnk"
    monkeypatch.setattr(desktop, "_start_menu_shortcuts", lambda: [tmp_path / "Spotify Uninstaller.lnk", lnk])
    monkeypatch.setattr(desktop, "_store_apps", lambda: [{"Name": "Xbox", "AppID": "Microsoft.GamingApp!Xbox"}])
    assert desktop.find_app("spotify") == str(lnk)
    assert desktop.find_app("xbox") == "shell:AppsFolder\\Microsoft.GamingApp!Xbox"
    assert desktop.find_app("ms-settings:display") == "ms-settings:display"
    assert desktop.find_app("steam://run/570") == "steam://run/570"
    assert desktop.find_app(r"C:\nowhere\game.exe") is None
    monkeypatch.setattr(desktop.shutil, "which", lambda name: r"C:\Windows\notepad.exe" if name == "notepad" else None)
    assert desktop.find_app("notepad") == r"C:\Windows\notepad.exe"


def test_a_hung_app_call_doesnt_block_the_next(monkeypatch):
    from backend.agency import uia

    monkeypatch.setattr(uia, "RUN_TIMEOUT", 0.3)
    with pytest.raises(desktop.DesktopError, match="didn't respond"):
        uia.run(time.sleep, 2)
    started = time.time()
    assert uia.run(lambda: 42) == 42
    assert time.time() - started < 1


# --- "did you mean...?" for near-matches ---------------------------------------------------

def test_near_matches_contain_or_sound_alike():
    names = ["Spotify", "Notepad", "Notepad++", "Steam", "Discord"]
    assert safety.near_matches("note", names) == ["Notepad", "Notepad++"]
    assert safety.near_matches("spotfy", names) == ["Spotify"]
    assert safety.near_matches("photoshop", names) == []


@pytest.fixture
def apps(monkeypatch, tmp_path):
    monkeypatch.setattr(desktop.shutil, "which", lambda name: None)
    lnks = [tmp_path / "Notepad++.lnk", tmp_path / "Spotify.lnk"]
    monkeypatch.setattr(desktop, "_start_menu_shortcuts", lambda: lnks)
    monkeypatch.setattr(desktop, "_store_apps", lambda: [])
    return lnks


def test_a_partial_app_name_is_asked_about_not_guessed(apps):
    with pytest.raises(safety.AskZero, match=r"Did you mean Notepad\+\+\?"):
        desktop.find_app("note")
    # Her own pick from the offered matches waits for Zero.
    with pytest.raises(safety.AskZero, match="hasn't confirmed"):
        desktop.find_app("Notepad++")
    safety.note_user_message("yes, notepad++")
    assert desktop.find_app("Notepad++") == str(apps[0])


def test_a_misheard_app_is_asked_about_and_isnt_a_failure(apps):
    out = run(tools.call("open_item", {"target": "spotfy"}))
    assert not out["ok"] and "Did you mean Spotify?" in out["result"]
    assert not safety._failures  # a question for Zero, not a failure


def test_an_exact_app_name_needs_no_question(apps):
    assert desktop.find_app("spotify") == str(apps[1])


def _win(hwnd, title, app):
    return {"hwnd": hwnd, "title": title, "pid": hwnd, "app": app, "minimized": False}


def test_windows_of_different_apps_are_asked_about(monkeypatch):
    monkeypatch.setattr(desktop, "windows", lambda: [
        _win(1, "notes.txt - Notepad", "notepad.exe"), _win(2, "todo.txt - Notepad++", "notepad++.exe")])
    assert desktop.find("notepad")["hwnd"] == 1  # the app's exact name
    with pytest.raises(safety.AskZero, match="notes.txt - Notepad, todo.txt - Notepad\\+\\+"):
        desktop.find("note")


def test_several_windows_of_one_app_are_not_a_question(monkeypatch):
    monkeypatch.setattr(desktop, "windows", lambda: [
        _win(1, "a.txt - Notepad", "notepad.exe"), _win(2, "b.txt - Notepad", "notepad.exe")])
    assert desktop.find("txt")["hwnd"] == 1


def test_a_file_that_isnt_quite_there_is_asked_about(tmp_path):
    (tmp_path / "Resume 2026.docx").write_text("cv")
    out = run(tools.call("read_file", {"path": str(tmp_path / "resume.docx")}))
    assert not out["ok"] and "Did you mean Resume 2026.docx?" in out["result"]
    assert not safety._failures
    opened = run(tools.call("open_item", {"target": str(tmp_path / "resume.docx")}))
    assert not opened["ok"] and "Did you mean" in opened["result"]


def test_a_file_with_nothing_like_it_is_just_missing(tmp_path):
    (tmp_path / "Resume 2026.docx").write_text("cv")
    out = run(tools.call("read_file", {"path": str(tmp_path / "taxes.xlsx")}))
    assert not out["ok"] and "doesn't exist" in out["result"]
    assert len(safety._failures) == 1
