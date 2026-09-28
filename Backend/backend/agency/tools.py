"""The tools Sarah can use, and the tools she makes for herself.

Every tool returns text for the model. Calls are logged (``agent_actions``
table), guarded (``guard.py``), time-limited, and stopped by the Stop
button. Tools she writes with ``create_tool`` live in her workspace
(``Backend/data/sarah_workspace/tools``) and become real tools from the next
step on, run in a separate process with her own Python environment.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import subprocess
import sys
import textwrap
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from . import guard
from .guard import Blocked, TOOLS_DIR, WORKSPACE

logger = logging.getLogger("sarah.agency")

MAX_RESULT_CHARS = 8000
_stop_until = 0.0


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

@dataclass
class Tool:
    name: str
    description: str
    parameters: Dict[str, Any]
    fn: Callable[..., Any]
    timeout: float = 60
    custom: bool = False
    is_async: bool = False

    def spec(self) -> Dict[str, Any]:
        return {"type": "function", "function": {
            "name": self.name, "description": self.description, "parameters": self.parameters}}


BUILTIN: Dict[str, Tool] = {}


def tool(name: str, description: str, properties: Dict[str, Any], required: Optional[List[str]] = None,
         timeout: float = 60):
    def wrap(fn):
        BUILTIN[name] = Tool(
            name=name, description=description, fn=fn, timeout=timeout,
            is_async=asyncio.iscoroutinefunction(fn),
            parameters={"type": "object", "properties": properties, "required": required or []},
        )
        return fn
    return wrap


def _custom_tools() -> Dict[str, Tool]:
    tools: Dict[str, Tool] = {}
    if not TOOLS_DIR.exists():
        return tools
    for manifest in TOOLS_DIR.glob("*.json"):
        try:
            meta = json.loads(manifest.read_text(encoding="utf-8"))
            name = meta["name"]
            if name in BUILTIN or not (TOOLS_DIR / f"{name}.py").exists():
                continue
            tools[name] = Tool(
                name=name, description=f"[your own tool] {meta.get('description', '')}",
                parameters=meta.get("parameters") or {"type": "object", "properties": {}},
                fn=lambda _n=name, **kw: _run_custom(_n, kw), timeout=float(meta.get("timeout", 120)), custom=True,
            )
        except Exception as exc:
            logger.warning("bad tool manifest %s: %s", manifest.name, exc)
    return tools


def all_tools() -> Dict[str, Tool]:
    return {**BUILTIN, **_custom_tools()}


def specs() -> List[Dict[str, Any]]:
    return [t.spec() for t in all_tools().values()]


# ---------------------------------------------------------------------------
# Calling, logging, stopping
# ---------------------------------------------------------------------------

def stop(seconds: float = 60) -> None:
    """The Stop button: refuse every tool for a while."""
    global _stop_until
    _stop_until = time.time() + seconds


def resume() -> None:
    global _stop_until
    _stop_until = 0.0


def stopped() -> bool:
    return time.time() < _stop_until


def _log_action(name: str, args: Dict[str, Any], ok: bool, result: str, ms: int) -> None:
    try:
        from backend.models.core import get_connection

        conn = get_connection()
        try:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS agent_actions (id INTEGER PRIMARY KEY AUTOINCREMENT,"
                " created_at TEXT DEFAULT CURRENT_TIMESTAMP, tool TEXT, args TEXT, ok INTEGER,"
                " result TEXT, ms INTEGER)"
            )
            conn.execute(
                "INSERT INTO agent_actions (tool, args, ok, result, ms) VALUES (?, ?, ?, ?, ?)",
                (name, json.dumps(args, default=str)[:2000], int(ok), result[:2000], ms),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception as exc:
        logger.debug("action not logged: %s", exc)


def recent_actions(limit: int = 30) -> List[Dict[str, Any]]:
    try:
        from backend.models.core import get_connection

        conn = get_connection()
        try:
            rows = conn.execute(
                "SELECT id, created_at, tool, args, ok, result, ms FROM agent_actions ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        finally:
            conn.close()
        return [dict(zip(("id", "created_at", "tool", "args", "ok", "result", "ms"), tuple(r))) for r in rows]
    except Exception:
        return []


def _as_text(value: Any) -> str:
    if isinstance(value, str):
        text = value
    else:
        text = json.dumps(value, ensure_ascii=False, default=str, indent=1)
    if len(text) > MAX_RESULT_CHARS:
        text = text[:MAX_RESULT_CHARS] + f"\n...[truncated, {len(text)} chars total]"
    return text


async def call(name: str, args: Dict[str, Any]) -> Dict[str, Any]:
    """Run one tool. Returns {"ok", "result", "ms"}; never raises."""
    started = time.time()
    t = all_tools().get(name)
    if t is None:
        return {"ok": False, "result": f"No tool named {name}. Available: {', '.join(sorted(all_tools()))}", "ms": 0}
    if stopped():
        return {"ok": False, "result": "Zero pressed Stop: actions are paused. Don't retry; tell Zero.", "ms": 0}
    try:
        if t.is_async:
            value = await asyncio.wait_for(t.fn(**args), timeout=t.timeout)
        else:
            value = await asyncio.wait_for(asyncio.to_thread(t.fn, **args), timeout=t.timeout)
        ok, text = True, _as_text(value)
    except Blocked as exc:
        ok, text = False, str(exc)
    except asyncio.TimeoutError:
        ok, text = False, f"Timed out after {int(t.timeout)} s."
    except TypeError as exc:
        ok, text = False, f"Bad arguments for {name}: {exc}"
    except Exception as exc:
        ok, text = False, f"{type(exc).__name__}: {exc}"
    ms = int((time.time() - started) * 1000)
    _log_action(name, args, ok, text, ms)
    if ok and name not in ("list_my_tools", "list_my_skills", "look"):
        try:  # her memory of the day: what she did
            from backend.memory.journal import experience
            brief = ", ".join(f"{k}={str(v)[:60]}" for k, v in list(args.items())[:2] if k not in ("code", "content"))
            experience("did", f"used {name}" + (f" ({brief})" if brief else ""))
        except Exception:
            pass
    logger.info("[AGENCY] %s %s -> %s (%d ms)", name, json.dumps(args, default=str)[:160], "ok" if ok else "failed", ms)
    return {"ok": ok, "result": text, "ms": ms}


# ---------------------------------------------------------------------------
# Her own Python environment (packages she installs, code and tools she runs)
# ---------------------------------------------------------------------------

def _env() -> Dict[str, str]:
    env = os.environ.copy()
    temp = os.environ.get("TEMP") or str(WORKSPACE / "tmp")
    env.update({"PIP_NO_CACHE_DIR": "1", "TEMP": temp, "TMP": temp, "PYTHONIOENCODING": "utf-8",
                "PYTHONPATH": str(WORKSPACE)})
    return env


def workspace_python() -> str:
    venv = WORKSPACE / ".venv" / "Scripts" / "python.exe"
    if venv.exists():
        return str(venv)
    guard.ensure_workspace()
    subprocess.run([sys.executable, "-m", "venv", "--system-site-packages", str(WORKSPACE / ".venv")],
                   check=True, capture_output=True, timeout=180)
    return str(venv)


def _write_guard_hook() -> None:
    """The guardrails, as an audit hook imported first by all her code."""
    roots = guard._system_roots()
    install = guard._norm(guard.REPO_ROOT)
    workspace = guard._norm(WORKSPACE)
    patterns = [p for p, _ in guard._BLOCKED_COMMANDS]
    code = f'''\
# Generated by backend/agency/tools.py: Sarah's guardrails inside her own code.
import os, re, sys
_ROOTS = {roots!r}
_INSTALL, _WORKSPACE = {install!r}, {workspace!r}
_CMDS = [re.compile(p) for p in {patterns!r}]
def _n(p):
    try:
        p = os.path.abspath(os.fsdecode(p))
    except Exception:
        return ""
    return p.rstrip("\\\\/").lower()
def _under(p, r):
    return p == r or p.startswith(r + "\\\\")
def _protected(p):
    p = _n(p)
    if not p:
        return False
    if any(_under(p, r) for r in _ROOTS):
        return True
    return _under(p, _INSTALL) and not _under(p, _WORKSPACE)
_WRITE_FLAGS = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND
def _hook(event, args):
    if event == "open":
        path, mode, flags = (list(args) + [None, None, None])[:3]
        writing = (isinstance(mode, str) and any(c in mode for c in "wax+")) or (isinstance(flags, int) and flags & _WRITE_FLAGS)
        if writing and isinstance(path, (str, bytes, os.PathLike)) and _protected(path):
            raise PermissionError(f"Refused by Sarah's guardrails: {{path}} is a protected system/program location")
    elif event in ("os.remove", "os.rmdir", "os.rename", "os.replace", "shutil.rmtree", "os.mkdir", "os.chmod", "os.truncate"):
        for p in args[:2]:
            if isinstance(p, (str, bytes, os.PathLike)) and _protected(p):
                raise PermissionError(f"Refused by Sarah's guardrails: {{p}} is a protected system/program location")
    elif event == "subprocess.Popen":
        cmd = args[1] if len(args) > 1 else ""
        text = " ".join(map(str, cmd)) if isinstance(cmd, (list, tuple)) else str(cmd)
        if any(r.search(text.lower()) for r in _CMDS):
            raise PermissionError("Refused by Sarah's guardrails: that command changes system hardware/software")
    elif event.startswith("winreg.") and event.split(".")[1] in ("CreateKey", "DeleteKey", "DeleteValue", "SetValue", "SetValueEx", "CreateKeyEx", "DeleteKeyEx"):
        raise PermissionError("Refused by Sarah's guardrails: registry changes are off limits")
sys.addaudithook(_hook)
'''
    guard.ensure_workspace()
    (WORKSPACE / "sarah_guard_hook.py").write_text(code, encoding="utf-8")


def _run_python_file(path: Path, args: List[str], timeout: float) -> Dict[str, Any]:
    _write_guard_hook()
    proc = subprocess.run(
        [workspace_python(), "-X", "utf8", str(path), *args],
        cwd=str(WORKSPACE), env=_env(), capture_output=True, text=True, encoding="utf-8",
        errors="replace", timeout=timeout,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    return {"exit_code": proc.returncode, "stdout": proc.stdout[-6000:], "stderr": proc.stderr[-3000:]}


def _run_custom(name: str, kwargs: Dict[str, Any]) -> Any:
    runner = WORKSPACE / "runs" / "_tool_runner.py"
    runner.parent.mkdir(parents=True, exist_ok=True)
    runner.write_text(textwrap.dedent(f'''\
        import sarah_guard_hook, json, sys, importlib
        sys.path.insert(0, {str(TOOLS_DIR)!r})
        mod = importlib.import_module(sys.argv[1])
        result = mod.run(**json.loads(sys.argv[2]))
        print("__SARAH_RESULT__" + json.dumps(result, default=str, ensure_ascii=False))
        '''), encoding="utf-8")
    out = _run_python_file(runner, [name, json.dumps(kwargs)], timeout=120)
    marker = "__SARAH_RESULT__"
    if marker in out["stdout"]:
        before, _, payload = out["stdout"].rpartition(marker)
        try:
            result = json.loads(payload.strip())
        except ValueError:
            result = payload.strip()
        return {"result": result, "printed": before.strip()[-2000:]} if before.strip() else result
    return {"error": "the tool crashed", **out}


# ---------------------------------------------------------------------------
# Built-in tools
# ---------------------------------------------------------------------------

@tool("web_search", "Search the web (DuckDuckGo). Returns titles, links and snippets.",
      {"query": {"type": "string"}, "max_results": {"type": "integer", "description": "1-10, default 5"}},
      ["query"], timeout=30)
def web_search(query: str, max_results: int = 5):
    from ddgs import DDGS

    results = DDGS().text(query, max_results=max(1, min(10, int(max_results or 5))))
    return [{"title": r.get("title"), "url": r.get("href"), "snippet": r.get("body")} for r in results]


@tool("read_webpage", "Fetch a web page and return its main text (articles, docs, forum posts).",
      {"url": {"type": "string"}, "max_chars": {"type": "integer", "description": "default 6000"}},
      ["url"], timeout=40)
def read_webpage(url: str, max_chars: int = 6000):
    import trafilatura

    if not re.match(r"^https?://", url or ""):
        raise ValueError("url must start with http:// or https://")
    html = trafilatura.fetch_url(url)
    if not html:
        return f"Could not fetch {url}"
    text = trafilatura.extract(html, include_links=False, include_tables=True, favor_recall=True) or ""
    limit = max(500, min(20000, int(max_chars or 6000)))
    return text[:limit] + ("\n...[more on the page]" if len(text) > limit else "")


@tool("run_python", "Run a Python script in your workspace (your own environment; install packages "
      "with install_package). Use it for calculations, data, files, automation. Print what you need "
      "to see. The working directory is your workspace.",
      {"code": {"type": "string"}, "timeout": {"type": "integer", "description": "seconds, default 60, max 600"}},
      ["code"], timeout=610)
def run_python(code: str, timeout: int = 60):
    runs = WORKSPACE / "runs"
    runs.mkdir(parents=True, exist_ok=True)
    path = runs / f"run_{datetime.now():%Y%m%d_%H%M%S_%f}.py"
    path.write_text("import sarah_guard_hook\n" + code, encoding="utf-8")
    return _run_python_file(path, [], timeout=max(5, min(600, int(timeout or 60))))


@tool("run_shell", "Run a PowerShell command on this PC and return its output. Changes to system "
      "hardware/software (drivers, registry, services, boot, disks, security, installed programs) are "
      "refused.",
      {"command": {"type": "string"}, "timeout": {"type": "integer", "description": "seconds, default 60"},
       "cwd": {"type": "string", "description": "working directory (default: your workspace)"}},
      ["command"], timeout=610)
def run_shell(command: str, timeout: int = 60, cwd: Optional[str] = None):
    guard.check_command(command)
    proc = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", command],
        cwd=cwd or str(guard.ensure_workspace()), env=_env(), capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=max(5, min(600, int(timeout or 60))),
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    return {"exit_code": proc.returncode, "stdout": proc.stdout[-6000:], "stderr": proc.stderr[-3000:]}


@tool("read_file", "Read a text file.", {"path": {"type": "string"},
      "max_chars": {"type": "integer", "description": "default 20000"}}, ["path"], timeout=20)
def read_file(path: str, max_chars: int = 20000):
    p = Path(os.path.expandvars(os.path.expanduser(path)))
    data = p.read_text(encoding="utf-8", errors="replace")
    limit = max(200, min(100000, int(max_chars or 20000)))
    return data[:limit] + (f"\n...[{len(data)} chars total]" if len(data) > limit else "")


@tool("list_directory", "List a folder (names, sizes, modified times).",
      {"path": {"type": "string"}}, ["path"], timeout=20)
def list_directory(path: str):
    p = Path(os.path.expandvars(os.path.expanduser(path)))
    items = []
    for child in sorted(p.iterdir(), key=lambda c: (not c.is_dir(), c.name.lower()))[:300]:
        try:
            st = child.stat()
            items.append({"name": child.name + ("/" if child.is_dir() else ""), "size": st.st_size,
                          "modified": datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M")})
        except OSError:
            items.append({"name": child.name})
    return {"path": str(p), "items": items}


@tool("write_file", "Create or overwrite (or append to) a text file.",
      {"path": {"type": "string"}, "content": {"type": "string"}, "append": {"type": "boolean"}},
      ["path", "content"], timeout=20)
def write_file(path: str, content: str, append: bool = False):
    p = Path(os.path.expandvars(os.path.expanduser(path)))
    guard.check_write(p)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a" if append else "w", encoding="utf-8") as fh:
        fh.write(content)
    return f"Wrote {len(content)} chars to {p}"


@tool("move_path", "Move or rename a file or folder.",
      {"source": {"type": "string"}, "destination": {"type": "string"}}, ["source", "destination"], timeout=60)
def move_path(source: str, destination: str):
    import shutil

    guard.check_write(source)
    guard.check_write(destination)
    return f"Moved to {shutil.move(os.path.expanduser(source), os.path.expanduser(destination))}"


@tool("delete_path", "Delete a file or folder (it goes to the Recycle Bin, so it can be restored).",
      {"path": {"type": "string"}}, ["path"], timeout=60)
def delete_path(path: str):
    from send2trash import send2trash

    p = os.path.expandvars(os.path.expanduser(path))
    guard.check_write(p)
    if not os.path.exists(p):
        return f"{p} doesn't exist"
    send2trash(p)
    return f"Moved {p} to the Recycle Bin"


@tool("open_item", "Open a URL in the browser, a file with its default app, or launch an app by "
      "name or path (e.g. 'notepad', 'spotify', 'C:/Games/game.exe').",
      {"target": {"type": "string"}}, ["target"], timeout=20)
def open_item(target: str):
    target = (target or "").strip()
    if re.match(r"^(https?|mailto|spotify|steam|discord)://", target) or os.path.exists(os.path.expanduser(target)):
        os.startfile(os.path.expanduser(target))
    else:
        subprocess.Popen(["cmd", "/c", "start", "", target], creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    return f"Opened {target}"


@tool("control_input", "Use Zero's mouse and keyboard. action: type (text), press (key, e.g. 'enter'), "
      "hotkey (keys like ['ctrl','s']), click (x, y, optional button), move (x, y), scroll (amount), "
      "screen_size. Look at the screen first so you know where things are.",
      {"action": {"type": "string", "enum": ["type", "press", "hotkey", "click", "double_click", "move", "scroll", "screen_size"]},
       "text": {"type": "string"}, "key": {"type": "string"}, "keys": {"type": "array", "items": {"type": "string"}},
       "x": {"type": "integer"}, "y": {"type": "integer"}, "button": {"type": "string"}, "amount": {"type": "integer"}},
      ["action"], timeout=30)
def control_input(action: str, text: str = "", key: str = "", keys: Optional[List[str]] = None,
                  x: Optional[int] = None, y: Optional[int] = None, button: str = "left", amount: int = 0):
    import pyautogui

    pyautogui.FAILSAFE = True  # slam the mouse into a corner to abort
    if action == "screen_size":
        w, h = pyautogui.size()
        return {"width": w, "height": h}
    if action == "type":
        pyautogui.write(text, interval=0.01)
    elif action == "press":
        pyautogui.press(key)
    elif action == "hotkey":
        pyautogui.hotkey(*(keys or []))
    elif action in ("click", "double_click"):
        (pyautogui.doubleClick if action == "double_click" else pyautogui.click)(x=x, y=y, button=button or "left")
    elif action == "move":
        pyautogui.moveTo(x, y, duration=0.2)
    elif action == "scroll":
        pyautogui.scroll(int(amount or 0))
    return f"Done: {action}"


@tool("look", "Take a fresh look through your eyes and answer a question about what you see "
      "(source: screen, camera or both). Use it before clicking or when you need details.",
      {"question": {"type": "string"}, "source": {"type": "string", "enum": ["screen", "camera", "both"]}},
      ["question"], timeout=60)
async def look(question: str, source: str = "screen"):
    from .senses import senses

    kinds = ["screen", "camera"] if source == "both" else [source or "screen"]
    seen = await senses.request("look", {"kinds": kinds, "question": question}, timeout=55)
    if not seen:
        return "Your eyes are closed or not connected right now."
    return seen


@tool("remember", "Save a lasting fact about Zero or something to remember across conversations.",
      {"fact": {"type": "string"}}, ["fact"], timeout=15)
def remember(fact: str):
    from backend.models.core import add_memory

    add_memory("assistant", fact.strip(), tags="sarah", importance=1)
    return "Remembered."


@tool("set_reminder", "Remind Zero (or yourself) about something later. Give minutes from now or an "
      "ISO time ('2026-09-28T09:00').",
      {"text": {"type": "string"}, "minutes": {"type": "number"}, "at": {"type": "string"}}, ["text"], timeout=15)
def set_reminder(text: str, minutes: Optional[float] = None, at: Optional[str] = None):
    from .reminders import add_reminder

    when = datetime.fromisoformat(at) if at else datetime.now() + timedelta(minutes=float(minutes or 10))
    add_reminder(text, when)
    return f"Reminder set for {when:%Y-%m-%d %H:%M}"


@tool("install_package", "Install a Python package into your own environment (for run_python and "
      "your tools).", {"package": {"type": "string", "description": "pip name, optionally ==version"}},
      ["package"], timeout=600)
def install_package(package: str):
    if not re.match(r"^[A-Za-z0-9._\-\[\]]+([=<>!~]=?[A-Za-z0-9.*+!\-]+)?$", package or ""):
        raise ValueError("not a valid package spec")
    proc = subprocess.run(
        [workspace_python(), "-m", "pip", "install", "--no-cache-dir", "--disable-pip-version-check", package],
        env=_env(), capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=590,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    tail = (proc.stdout + proc.stderr).strip().splitlines()[-4:]
    return {"ok": proc.returncode == 0, "output": "\n".join(tail)}


_TOOL_NAME = re.compile(r"^[a-z][a-z0-9_]{2,40}$")


@tool("create_tool", "Make yourself a new tool when none of yours fits. Write Python defining "
      "`def run(**kwargs)` that returns JSON-able data; describe its parameters as a JSON schema. It is "
      "tested with test_args if given, then available as a tool from your next step on.",
      {"name": {"type": "string", "description": "snake_case"}, "description": {"type": "string"},
       "parameters": {"type": "object", "description": "JSON schema of kwargs"},
       "code": {"type": "string"}, "test_args": {"type": "object"}},
      ["name", "description", "code"], timeout=180)
def create_tool(name: str, description: str, code: str, parameters: Optional[Dict[str, Any]] = None,
                test_args: Optional[Dict[str, Any]] = None):
    if not _TOOL_NAME.match(name or "") or name in BUILTIN:
        raise ValueError("name must be new snake_case (3-40 chars) and not a built-in tool")
    if not re.search(r"^def run\(", code, re.M):
        raise ValueError("code must define `def run(**kwargs)` at top level")
    guard.ensure_workspace()
    compile(code, f"{name}.py", "exec")
    source = "import sarah_guard_hook\n" + code
    existing = TOOLS_DIR / f"{name}.py"
    if existing.exists() and existing.read_text(encoding="utf-8") == source and test_args is None:
        return {"created": name, "note": "identical tool already exists; use it"}
    existing.write_text(source, encoding="utf-8")
    manifest = {"name": name, "description": description[:500],
                "parameters": parameters or {"type": "object", "properties": {}},
                "created_at": datetime.now().isoformat(timespec="seconds")}
    (TOOLS_DIR / f"{name}.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    report: Dict[str, Any] = {"created": name}
    if test_args is not None:
        report["test"] = _run_custom(name, test_args)
    return report


# ---------------------------------------------------------------------------
# Skills: know-how in her own repertoire (OpenClaw / Agent Skills format)
# ---------------------------------------------------------------------------

def _find_skill(name: str):
    from backend.skills import get_all_skills

    key = (name or "").strip().lower()
    for s in get_all_skills():
        if not s.stale and (s.slug == key or s.name.lower() == key):
            return s
    return None


@tool("add_skill", "Learn a new skill into your own repertoire: an OpenClaw/ClawHub or Agent Skills "
      "skill from a GitHub folder URL, a SKILL.md link, a .zip link, or a local folder/zip. The whole "
      "skill (instructions + scripts) is copied into your repertoire. A repo URL lists the skills in it.",
      {"source": {"type": "string"}, "overwrite": {"type": "boolean", "description": "replace an existing one"}},
      ["source"], timeout=120)
def add_skill(source: str, overwrite: bool = False):
    from backend.skills import reload
    from backend.skills.installer import install_skill

    try:
        installed = install_skill(source, overwrite=bool(overwrite))
    except LookupError as exc:  # a repo with several skills: let her choose
        return str(exc)
    reload()
    return {"learned": installed["slug"], "name": installed["name"], "description": installed["description"],
            "files": installed["files"][:40]}


@tool("use_skill", "Open one of your skills: returns its full instructions and the files in its "
      "folder (run its scripts with run_python/run_shell, using the folder path given).",
      {"name": {"type": "string", "description": "skill slug or name"}}, ["name"], timeout=15)
def use_skill(name: str):
    s = _find_skill(name)
    if s is None:
        from backend.skills import get_enabled_skills
        return f"You don't have a skill called {name}. Yours: {', '.join(x.slug for x in get_enabled_skills()) or 'none yet'}"
    folder = Path(s.path).parent if s.path else None
    files = sorted(str(p.relative_to(folder)).replace("\\", "/") for p in folder.rglob("*") if p.is_file())[:80] if folder else []
    return {"skill": s.slug, "name": s.name, "folder": str(folder) if folder else None,
            "enabled": s.enabled, "files": files, "instructions": s.body}


@tool("list_my_skills", "List the skills in your repertoire.", {}, timeout=10)
def list_my_skills():
    from backend.skills import get_all_skills
    return [{"skill": s.slug, "description": s.description, "enabled": s.enabled}
            for s in get_all_skills() if not s.stale]


@tool("remove_skill", "Forget a skill (its folder goes to the Recycle Bin).",
      {"name": {"type": "string"}}, ["name"], timeout=20)
def remove_skill(name: str):
    from send2trash import send2trash
    from backend.skills import reload

    s = _find_skill(name)
    if s is None or not s.path:
        return f"No skill called {name}"
    send2trash(str(Path(s.path).parent))
    reload()
    return f"Removed {s.slug}"


@tool("list_my_tools", "List the tools you've made for yourself.", {}, timeout=10)
def list_my_tools():
    return [{"name": t.name, "description": t.description} for t in _custom_tools().values()]


@tool("delete_my_tool", "Remove one of your own tools (to the Recycle Bin).",
      {"name": {"type": "string"}}, ["name"], timeout=20)
def delete_my_tool(name: str):
    from send2trash import send2trash

    removed = []
    for ext in (".py", ".json"):
        p = TOOLS_DIR / f"{name}{ext}"
        if p.exists():
            send2trash(str(p))
            removed.append(p.name)
    return f"Removed {', '.join(removed)}" if removed else f"No tool named {name}"
