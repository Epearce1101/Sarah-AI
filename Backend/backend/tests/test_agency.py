"""Sarah's agency: guardrails, tools she uses and makes, the tool loop."""
import asyncio
import json
import os
from types import SimpleNamespace

import pytest

from backend.agency import guard, tools
from backend.agency.guard import Blocked


# --- guardrails ------------------------------------------------------------------

@pytest.mark.parametrize("path", [
    r"C:\Windows\System32\drivers\etc\hosts",
    r"C:\Program Files\Something\app.exe",
    r"C:\ProgramData\Microsoft\x.dat",
    r"C:\\",
    r"E:\AI\Sarah_V10\Backend\backend\app.py",          # her own program files
    r"E:\AI\Sarah_V10\frontend\main.js",
])
def test_protected_paths(path):
    with pytest.raises(Blocked):
        guard.check_write(path)


@pytest.mark.parametrize("path", [
    r"E:\AI\Sarah_V10\Backend\data\sarah_workspace\notes.txt",  # her workspace is hers
    os.path.join(os.path.expanduser("~"), "Documents", "shopping.txt"),
    r"D:\Games\save.dat",
])
def test_user_paths_are_allowed(path):
    guard.check_write(path)


@pytest.mark.parametrize("command", [
    "reg add HKLM\\Software\\X /v Y /d 1",
    "Set-ItemProperty -Path HKLM:\\SYSTEM\\X -Name Y -Value 1",
    "bcdedit /set {current} safeboot minimal",
    "diskpart /s script.txt",
    "Stop-Service -Name WinDefend",
    "sc.exe delete SomeService",
    "Set-MpPreference -DisableRealtimeMonitoring $true",
    "netsh advfirewall set allprofiles state off",
    "winget uninstall Spotify",
    "shutdown /s /t 0",
    "Remove-Item -Recurse -Force C:\\Windows\\Temp\\x",
    "del \"C:\\Program Files\\App\\app.exe\"",
    "Remove-Item E:\\AI\\Sarah_V10\\Backend\\backend\\app.py",
    "pip uninstall numpy",
])
def test_blocked_commands(command):
    with pytest.raises(Blocked):
        guard.check_command(command)


@pytest.mark.parametrize("command", [
    "Get-ChildItem $HOME\\Downloads",
    "Get-Process | Sort-Object CPU -Descending | Select-Object -First 5",
    "Move-Item $HOME\\Downloads\\a.pdf $HOME\\Documents\\",
    "Get-Content C:\\Windows\\System32\\drivers\\etc\\hosts",  # reading is fine
    "python --version",
])
def test_everyday_commands_are_allowed(command):
    guard.check_command(command)


# --- tools ---------------------------------------------------------------------

def run(coro):
    return asyncio.run(coro)


def test_specs_are_openai_tools():
    specs = tools.specs()
    names = {s["function"]["name"] for s in specs}
    assert {"web_search", "read_webpage", "run_python", "run_shell", "create_tool", "look", "control_input"} <= names
    assert all(s["type"] == "function" and s["function"]["parameters"]["type"] == "object" for s in specs)


def test_unknown_tool_and_stop_button():
    out = run(tools.call("teleport", {}))
    assert not out["ok"] and "No tool named teleport" in out["result"]
    tools.stop(30)
    try:
        out = run(tools.call("list_my_tools", {}))
        assert not out["ok"] and "Stop" in out["result"]
    finally:
        tools.resume()


def test_blocked_write_is_reported_not_raised(tmp_path):
    out = run(tools.call("write_file", {"path": r"C:\Windows\sarah_test.txt", "content": "x"}))
    assert not out["ok"] and "off limits" in out["result"]


def test_file_tools_and_recycle_bin(tmp_path):
    target = tmp_path / "note.txt"
    assert run(tools.call("write_file", {"path": str(target), "content": "hello"}))["ok"]
    assert "hello" in run(tools.call("read_file", {"path": str(target)}))["result"]
    listing = run(tools.call("list_directory", {"path": str(tmp_path)}))
    assert "note.txt" in listing["result"]
    out = run(tools.call("delete_path", {"path": str(target)}))
    assert out["ok"] and "Recycle Bin" in out["result"] and not target.exists()


def test_run_python_with_guardrails_inside_her_code():
    ok = run(tools.call("run_python", {"code": "print(6 * 7)", "timeout": 60}))
    assert ok["ok"] and '"stdout": "42' in ok["result"].replace("\\n", "")
    blocked = run(tools.call("run_python", {"code": "open(r'C:\\\\Windows\\\\sarah_probe.txt', 'w').write('x')"}))
    assert "guardrails" in blocked["result"]


def test_create_test_and_use_her_own_tool(monkeypatch, tmp_path):
    monkeypatch.setattr(tools, "TOOLS_DIR", tmp_path)
    monkeypatch.setattr(guard, "TOOLS_DIR", tmp_path)
    code = "def run(text='', times=2):\n    return {'shout': (text.upper() + '! ') * int(times)}\n"
    made = run(tools.call("create_tool", {
        "name": "shout", "description": "Shout text", "code": code,
        "parameters": {"type": "object", "properties": {"text": {"type": "string"}, "times": {"type": "integer"}}},
        "test_args": {"text": "hi", "times": 3},
    }))
    assert made["ok"] and "HI! HI! HI!" in made["result"]
    assert "shout" in {s["function"]["name"] for s in tools.specs()}
    used = run(tools.call("shout", {"text": "hey", "times": 1}))
    assert used["ok"] and "HEY!" in used["result"]
    bad = run(tools.call("create_tool", {"name": "web_search", "description": "x", "code": code}))
    assert not bad["ok"]


# --- the tool loop in a streamed turn -------------------------------------------------

def _chunk(content=None, tool_calls=None, finish=None):
    delta = SimpleNamespace(content=content, tool_calls=tool_calls)
    return SimpleNamespace(model="fake:free", usage=None, choices=[SimpleNamespace(delta=delta, finish_reason=finish)])


def _tc(index, id=None, name=None, arguments=None):
    return SimpleNamespace(index=index, id=id, function=SimpleNamespace(name=name, arguments=arguments))


class FakeStream(list):
    def close(self):
        pass


def test_a_turn_that_uses_every_step_still_answers(monkeypatch):
    from backend.memory import openrouter_client as oc

    offered = []

    class Completions:
        def create(self, **kw):
            offered.append(bool(kw.get("tools")))
            if kw.get("tools"):  # keeps wanting another tool
                return FakeStream([_chunk(tool_calls=[_tc(0, f"c{len(offered)}", "list_my_tools", "{}")], finish="tool_calls")])
            return FakeStream([_chunk("Here's what I found so far.", finish="stop")])

    client = oc.OpenRouterClient.__new__(oc.OpenRouterClient)
    client._client = SimpleNamespace(chat=SimpleNamespace(completions=Completions()))
    client.config = SimpleNamespace(llm_max_completion_tokens=100, llm_temperature=0.5, chars_per_token=4)
    client._is_local_mode = lambda: False
    packet = SimpleNamespace(messages=[{"role": "user", "content": "dig into this"}], estimated_tokens=10, debug_info={})
    client._prepare_turn = lambda *a, **k: (packet, 1)
    client._finish_turn = lambda **kw: SimpleNamespace(content=kw["raw_content"])
    monkeypatch.setattr(oc.llm_models, "completion_kwargs", lambda: {"model": "fake:free", "extra_body": None})
    monkeypatch.setattr(tools, "_custom_tools", lambda: {})

    async def collect():
        return [e async for e in client.chat_stream(1, "dig into this")]

    events = run(collect())
    assert offered == [True] * client.MAX_TOOL_STEPS + [False]
    assert events[-1]["response"].content == "Here's what I found so far."


def test_relative_paths_live_in_her_workspace():
    out = run(tools.call("write_file", {"path": "notes/relative_test.txt", "content": "hi"}))
    assert out["ok"] and "sarah_workspace" in out["result"]
    assert run(tools.call("read_file", {"path": "notes/relative_test.txt"}))["result"] == "hi"
    assert run(tools.call("delete_path", {"path": "notes/relative_test.txt"}))["ok"]


def test_streamed_turn_calls_a_tool_then_answers(monkeypatch):
    from backend.memory import openrouter_client as oc

    rounds = [
        FakeStream([_chunk("Let me check. "),
                    _chunk(tool_calls=[_tc(0, "c1", "list_my_tools", "")]),
                    _chunk(tool_calls=[_tc(0, None, None, "{}")], finish="tool_calls")]),
        FakeStream([_chunk("You have "), _chunk("no tools yet.", finish="stop")]),
    ]
    seen_messages = []

    class Completions:
        def create(self, **kw):
            seen_messages.append(kw["messages"])
            assert kw.get("tools") and kw["tool_choice"] == "auto"
            return rounds.pop(0)

    client = oc.OpenRouterClient.__new__(oc.OpenRouterClient)
    client._client = SimpleNamespace(chat=SimpleNamespace(completions=Completions()))
    client.config = SimpleNamespace(llm_max_completion_tokens=100, llm_temperature=0.5, chars_per_token=4)
    client._is_local_mode = lambda: False
    packet = SimpleNamespace(messages=[{"role": "user", "content": "what tools do you have?"}],
                             estimated_tokens=10, debug_info={})
    client._prepare_turn = lambda *a, **k: (packet, 1)
    finished = {}
    client._finish_turn = lambda **kw: finished.update(kw) or SimpleNamespace(content=kw["raw_content"])
    monkeypatch.setattr(oc.llm_models, "completion_kwargs", lambda: {"model": "fake:free", "extra_body": None})
    monkeypatch.setattr(tools, "_custom_tools", lambda: {})

    async def collect():
        return [e async for e in client.chat_stream(1, "what tools do you have?")]

    events = run(collect())
    kinds = [e["type"] for e in events]
    assert kinds.count("tool") == 2 and kinds[-1] == "done"
    tool_done = [e for e in events if e["type"] == "tool" and e["status"] == "done"][0]
    assert tool_done["name"] == "list_my_tools" and tool_done["ok"]
    text = "".join(e["text"] for e in events if e["type"] == "delta")
    assert text == "Let me check. You have no tools yet."
    assert finished["raw_content"] == text
    # Round 2 saw the tool call and its result.
    second = seen_messages[1]
    assert second[-2]["tool_calls"][0]["function"]["name"] == "list_my_tools"
    assert second[-1]["role"] == "tool" and second[-1]["tool_call_id"] == "c1"


def test_code_task_runs_aider_and_reports_the_changes(tmp_path, monkeypatch):
    import subprocess as sp

    (tmp_path / ".git").mkdir()
    state = {"n": 0}

    def fake_git(root, *args):
        done = state["n"] > 0
        if args[0] == "diff":
            return "+def multiply" if done else ""
        return " M calc.py\n?? test_calc.py" if done else ""

    calls = []

    def fake_run(cmd, **kw):
        calls.append((cmd, kw))
        state["n"] += 1
        return sp.CompletedProcess(cmd, 0, stdout="Applied edit to calc.py", stderr="")

    monkeypatch.setattr(tools, "_git", fake_git)
    monkeypatch.setattr(tools, "git_exe", lambda: r"C:\git\git.exe")
    monkeypatch.setattr(tools.subprocess, "run", fake_run)
    out = run(tools.call("code_task", {"task": "add multiply", "folder": str(tmp_path), "files": ["calc.py"]}))
    result = json.loads(out["result"])
    cmd, kw = calls[0]
    assert "aider" in cmd and "--no-auto-commits" in cmd and cmd[-1] == "calc.py" and "--no-git" not in cmd
    assert cmd[cmd.index("--model") + 1].startswith("openrouter/")
    assert kw["cwd"] == str(tmp_path) and kw["env"]["GIT_PYTHON_GIT_EXECUTABLE"] == r"C:\git\git.exe"
    assert result["changed"] and "?? test_calc.py" in result["files"]
