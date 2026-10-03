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


# ---------------------------------------------------------------------------
# Did she check her work? (used by the tool loop before she answers)
# ---------------------------------------------------------------------------

_CHECK_ACTIONS = {
    "read_file": None, "list_directory": None, "look": None,
    "browser": {"read", "extract", "tables", "look", "tabs", "use_tab", "open"},
    "app": {"inspect", "read", "windows", "open", "save_as", "type"},
    "document": None, "window": {"list", "wait"},
}


def is_check(name: str, args: Dict[str, Any]) -> bool:
    """A call that looks at the result of earlier actions."""
    if name not in _CHECK_ACTIONS:
        return False
    actions = _CHECK_ACTIONS[name]
    return actions is None or str(args.get("action", "")) in actions


def unverified_action(name: str, args: Dict[str, Any], ok: bool, result: str) -> Optional[str]:
    """What she just did that changes things but carries no proof it worked."""
    action = str(args.get("action", ""))
    if name == "control_input" and action != "screen_size":
        return f"used the {'keyboard' if action in ('type', 'press', 'hotkey') else 'mouse'} ({action})"
    if name == "app" and action in ("keys", "menu", "click"):
        return f"{action} in {args.get('window', 'an app')}"
    if name == "browser" and action in ("click", "type", "select", "press") and "Nothing on the page changed" in result:
        return f"a browser {action} that didn't change the page"
    if name == "open_item" and ok and "Chrome tab" not in result:
        return f"opened {args.get('target', 'something')}"
    return None


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
    if ok and name not in ("list_my_tools", "list_my_skills", "look", "recall", "update_plan"):
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


def _resolve(path: str) -> Path:
    """A path she gives: absolute as-is; "Desktop/...", "Documents/...",
    "Downloads/..." (etc.) are Zero's real folders; other relative ones are
    inside her workspace (where run_python / run_shell work)."""
    p = Path(os.path.expandvars(os.path.expanduser(str(path).strip().strip('"'))))
    if p.is_absolute():
        return p
    parts = p.parts
    if parts:
        from .desktop import known_folder

        for n in (2, 1):  # "my documents/x" before "documents/x"
            if len(parts) >= n:
                base = known_folder(" ".join(parts[:n]))
                if base is not None:
                    return base.joinpath(*parts[n:])
    return WORKSPACE / p


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


THIN_PAGE = 400  # chars: less than this from a plain fetch -> it's probably built by JavaScript


def _extract(html: str) -> str:
    import trafilatura

    return (trafilatura.extract(html, output_format="markdown", include_tables=True, include_links=False,
                                favor_recall=True) or "") if html else ""


async def page_markdown(url: str) -> Dict[str, Any]:
    """A page's main content as Markdown (headings, lists, tables kept):
    a quick plain fetch, and if that comes back empty or thin (a JavaScript
    site), the page rendered in her own browser."""
    import trafilatura

    html = await asyncio.to_thread(trafilatura.fetch_url, url)
    text = await asyncio.to_thread(_extract, html)
    how = "fetched"
    if len(text) < THIN_PAGE:
        try:
            from .browser import browser
            rendered = await asyncio.wait_for(browser.render(url), timeout=45)
            better = await asyncio.to_thread(_extract, rendered)
            if len(better) > len(text):
                text, how = better, "rendered (JavaScript page)"
        except Exception as exc:
            logger.debug("render fallback failed for %s: %s", url, exc)
    return {"text": text, "how": how}


@tool("read_webpage", "Read a web page's main content as Markdown (articles, docs, forums, product pages; "
      "JavaScript-built pages are rendered in your own browser automatically).",
      {"url": {"type": "string"}, "max_chars": {"type": "integer", "description": "default 6000"}},
      ["url"], timeout=70)
async def read_webpage(url: str, max_chars: int = 6000):
    if not re.match(r"^https?://", url or ""):
        raise ValueError("url must start with http:// or https://")
    page = await page_markdown(url)
    text = page["text"]
    if not text:
        return f"Could not read anything from {url}"
    limit = max(500, min(20000, int(max_chars or 6000)))
    body = text[:limit] + ("\n...[more on the page]" if len(text) > limit else "")
    return f"({page['how']}, {len(text)} chars)\n\n{body}"


@tool("browser", "Real web browsing: pages that need JavaScript, clicking, typing, forms, scrolling. "
      "When Zero's Chrome is connected (the Sarah extension) this works right in their Chrome, in a tab "
      "of your own, with their logins; leave their other tabs alone unless they ask (then use_tab: the "
      "tab they're on, or tab_id from tabs). Otherwise it's your own separate browser. Actions: open "
      "(url), read (current page), find (text: words to look for; returns matching elements and lines, "
      "the fast way to locate a button/link/field), click (ref), type (ref + text, submit to press Enter), "
      "fill (fields: [{ref, text}] for whole forms; checkboxes take 'on'/'off'; submit), select (ref + option "
      "text), check (ref, text 'on'/'off'), hover (ref, opens hover menus), press (key), scroll (direction "
      "up/down, or ref to bring it into view), wait_for (text or selector to appear, timeout s: for pages "
      "that load slowly), back, forward, extract (scrape: CSS selector in text), tables, look (screenshot + "
      "question), close (your tab), tabs (list Chrome tabs), use_tab. Results list the page text and "
      "numbered elements [n] (with current values/checked state) to use as ref; 'changed' says whether the "
      "page reacted. where='own' forces your separate browser; visible=true shows that one.",
      {"action": {"type": "string", "enum": ["open", "read", "find", "click", "type", "fill", "select", "check",
                                             "hover", "press", "scroll", "wait_for", "back", "forward", "extract",
                                             "tables", "look", "close", "tabs", "use_tab"]},
       "url": {"type": "string"}, "ref": {"type": "integer"}, "text": {"type": "string"},
       "submit": {"type": "boolean"}, "key": {"type": "string"}, "direction": {"type": "string"},
       "question": {"type": "string"}, "visible": {"type": "boolean"}, "max_chars": {"type": "integer"},
       "selector": {"type": "string"}, "timeout": {"type": "number"},
       "fields": {"type": "array", "items": {"type": "object", "properties": {
           "ref": {"type": "integer"}, "text": {"type": "string"}}}},
       "tab_id": {"type": "integer"}, "where": {"type": "string", "enum": ["chrome", "own"]}},
      ["action"], timeout=90)
async def browser_tool(action: str, where: str = "chrome", **kwargs):
    from .chrome_bridge import bridge
    from .browser import browser

    if where != "own" and bridge.connected():
        return await bridge.act(action, **kwargs)
    if action in ("tabs", "use_tab"):
        return "Zero's Chrome isn't connected (the Sarah Browser Bridge extension), so you can't see their tabs."
    kwargs.pop("tab_id", None)
    return await browser.act(action, **kwargs)


def _page_text(url: str) -> str:
    import trafilatura

    html = trafilatura.fetch_url(url)
    return (trafilatura.extract(html, include_tables=True, favor_recall=True) or "") if html else ""


def _best_passages(text: str, question: str, limit: int = 1400) -> List[str]:
    words = {w for w in re.findall(r"[a-z0-9]{3,}", question.lower())}
    paras = [p.strip() for p in re.split(r"\n\s*\n|\n", text) if len(p.strip()) > 60]
    scored = sorted(paras, key=lambda p: -sum(1 for w in words if w in p.lower()))
    out, used = [], 0
    for p in scored:
        if used + len(p) > limit:
            continue
        out.append(p)
        used += len(p)
        if used > limit * 0.8:
            break
    return out


@tool("research", "Research a question on the web in one step: searches, reads several sources in "
      "parallel and returns the most relevant passages with their links. Cite sources as [n]. Use it for "
      "anything factual, current or specialised; follow up with read_webpage or browser for depth.",
      {"question": {"type": "string"}, "sources": {"type": "integer", "description": "pages to read, 2-8 (default 4)"},
       "query": {"type": "string", "description": "optional search query if different from the question"}},
      ["question"], timeout=90)
async def research(question: str, sources: int = 4, query: Optional[str] = None):
    from ddgs import DDGS
    from urllib.parse import urlparse

    n = max(2, min(8, int(sources or 4)))
    hits = await asyncio.to_thread(lambda: list(DDGS().text(query or question, max_results=n * 3)))
    picked, domains = [], set()
    for h in hits:
        dom = urlparse(h.get("href", "")).netloc
        if not dom or dom in domains:
            continue
        domains.add(dom)
        picked.append(h)
        if len(picked) >= n:
            break

    async def read(hit):
        try:
            return (await asyncio.wait_for(page_markdown(hit["href"]), timeout=40))["text"]
        except Exception:
            return ""

    texts = await asyncio.gather(*(read(h) for h in picked))
    found = []
    for i, (hit, text) in enumerate(zip(picked, texts), 1):
        passages = _best_passages(text, question) if text else []
        found.append({"n": i, "title": hit.get("title"), "url": hit.get("href"),
                      "passages": passages or [hit.get("body") or ""]})
    return {"question": question, "sources": found}


@tool("deep_research", "Thorough research for big or open questions (comparisons, 'what's the best...', "
      "how something works, the state of a topic): plans several searches, reads many sources, fills the gaps "
      "it finds, and writes a cited report (takes a minute or two). save_to (e.g. 'Documents/Report.docx' or "
      ".pdf/.md) saves it as a document. For a quick fact use research instead.",
      {"question": {"type": "string"}, "depth": {"type": "integer", "description": "1 = one round, 2 = also fill gaps (default)"},
       "save_to": {"type": "string"}},
      ["question"], timeout=420)
async def deep_research(question: str, depth: int = 2, save_to: str = ""):
    from . import deep_research as dr

    result = await dr.run(question, depth=depth)
    if save_to:
        from . import documents

        p = _resolve(save_to)
        guard.check_write(p)
        saved = await asyncio.to_thread(documents.write, p, result["report"], question)
        result["saved"] = {k: saved[k] for k in ("path", "bytes", "check") if k in saved}
    return result


@tool("code_task", "Hand a coding job to Aider (a pair-programming agent) working in a project folder: "
      "multi-file edits, new features, fixes, refactors, tests. Give the task in plain words, the folder, and "
      "the files to edit if you know them (it can find others itself). Runs on your free model; changes are "
      "NOT committed. Returns Aider's summary and the diff so you can check the work (read the files or run "
      "the tests after).",
      {"task": {"type": "string"}, "folder": {"type": "string"},
       "files": {"type": "array", "items": {"type": "string"}, "description": "files to edit, relative to folder"},
       "read_only": {"type": "array", "items": {"type": "string"}, "description": "files for context only"}},
      ["task", "folder"], timeout=900)
def code_task(task: str, folder: str, files: Optional[List[str]] = None, read_only: Optional[List[str]] = None):
    from backend import llm_models
    from backend.config import settings

    root = _resolve(folder)
    if not root.is_dir():
        raise ValueError(f"{root} is not a folder")
    guard.check_write(root / "x")
    git = git_exe()
    is_git = bool(git) and (root / ".git").exists()
    before = (_git(root, "diff"), _git(root, "status", "--porcelain")) if is_git else None
    cmd = [workspace_python(), "-m", "aider", "--model", f"openrouter/{llm_models.current_online_model()}",
           "--yes-always", "--no-auto-commits", "--no-pretty", "--no-stream", "--no-check-update",
           "--no-show-model-warnings", "--analytics-disable", "--no-fancy-input", "--map-tokens", "1024",
           "--message", task]
    if not is_git:
        cmd.append("--no-git")
    for f in read_only or []:
        cmd += ["--read", f]
    cmd += list(files or [])
    env = _env()
    env["OPENROUTER_API_KEY"] = settings.openrouter_api_key or os.environ.get("OPENROUTER_API_KEY", "")
    if git:
        env["GIT_PYTHON_GIT_EXECUTABLE"] = git
        env["PATH"] = str(Path(git).parent) + os.pathsep + env.get("PATH", "")
    proc = subprocess.run(cmd, cwd=str(root), env=env, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=880, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    log = re.sub(r"\n{3,}", "\n\n", (proc.stdout + "\n" + proc.stderr).strip())
    out: Dict[str, Any] = {"exit_code": proc.returncode, "aider_said": log[-4000:]}
    if is_git:
        diff, status = _git(root, "diff"), _git(root, "status", "--porcelain")
        out["changed"] = (diff, status) != before
        out["files"] = status.strip()[-1500:] or "(nothing changed)"   # " M" edited, "??" new
        out["diff"] = diff[:12000] or "(no edits to tracked files)"
    return out


def git_exe() -> Optional[str]:
    """git on PATH, or the portable MinGit this PC uses."""
    import shutil

    found = shutil.which("git")
    if found:
        return found
    for candidate in (r"E:\Tools\MinGit\cmd\git.exe", r"C:\Program Files\Git\cmd\git.exe"):
        if os.path.exists(candidate):
            return candidate
    return None


def _git(root: Path, *args: str) -> str:
    exe = git_exe()
    if not exe:
        return ""
    try:
        return subprocess.run([exe, *args], cwd=str(root), capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=30,
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
    except Exception:
        return ""


@tool("pc", "Everyday PC controls: volume (level 0-100, or change like +10/-10, or mute true/false; returns "
      "the level after), media (key: play_pause, next, previous, stop: for Spotify/YouTube/any player), "
      "clipboard_read, clipboard_write (text), screenshot (path, default Pictures/Sarah screenshots), stats "
      "(CPU, RAM, GPU load/temperature, free disk space, battery, uptime, the biggest programs running).",
      {"action": {"type": "string", "enum": ["volume", "media", "clipboard_read", "clipboard_write", "screenshot", "stats"]},
       "level": {"type": "number"}, "change": {"type": "number"}, "mute": {"type": "boolean"},
       "key": {"type": "string", "enum": ["play_pause", "next", "previous", "stop"]},
       "text": {"type": "string"}, "path": {"type": "string"}},
      ["action"], timeout=30)
def pc(action: str, level: Optional[float] = None, change: Optional[float] = None, mute: Optional[bool] = None,
       key: str = "", text: str = "", path: str = ""):
    from . import pc as pc_mod

    if action == "volume":
        return pc_mod.volume(level, change, mute)
    if action == "media":
        return pc_mod.media(key)
    if action == "clipboard_read":
        return pc_mod.clipboard_read()[:20000] or "(the clipboard is empty)"
    if action == "clipboard_write":
        return pc_mod.clipboard_write(text)
    if action == "screenshot":
        target = _resolve(path or f"Pictures/Sarah screenshots/{datetime.now():%Y-%m-%d %H-%M-%S}.png")
        guard.check_write(target)
        return pc_mod.screenshot(target)
    if action == "stats":
        return pc_mod.stats()
    raise ValueError(f"unknown pc action {action!r}")


@tool("http_request", "Call a web service or API directly (web bridging): GET/POST/PUT/PATCH/DELETE "
      "with optional headers, query params and a JSON or text body. Returns status and the response "
      "(JSON parsed when possible). Ask Zero before posting anything on their behalf.",
      {"url": {"type": "string"}, "method": {"type": "string", "enum": ["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD"]},
       "headers": {"type": "object"}, "params": {"type": "object"}, "json": {"type": "object"},
       "body": {"type": "string"}, "timeout": {"type": "integer"}},
      ["url"], timeout=70)
def http_request(url: str, method: str = "GET", headers: Optional[Dict[str, str]] = None,
                 params: Optional[Dict[str, Any]] = None, json: Optional[Any] = None,
                 body: Optional[str] = None, timeout: int = 30):
    import requests
    from urllib.parse import urlparse
    from backend.config import settings

    parsed = urlparse(url or "")
    if parsed.scheme not in ("http", "https"):
        raise ValueError("url must be http(s)")
    host = (parsed.hostname or "").lower()
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    if host in ("127.0.0.1", "localhost", "::1", "0.0.0.0") and port == int(getattr(settings, "backend_port", 8907)):
        raise Blocked("Refused: that's your own backend; its controls (like Stop) belong to Zero.")
    resp = requests.request((method or "GET").upper(), url, headers=headers or None, params=params or None,
                            json=json, data=body if json is None else None,
                            timeout=max(3, min(60, int(timeout or 30))))
    ctype = resp.headers.get("content-type", "")
    try:
        payload = resp.json() if "json" in ctype else resp.text
    except ValueError:
        payload = resp.text
    return {"status": resp.status_code, "content_type": ctype, "url": resp.url, "body": payload}


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
    p = _resolve(path)
    data = p.read_text(encoding="utf-8", errors="replace")
    limit = max(200, min(100000, int(max_chars or 20000)))
    return data[:limit] + (f"\n...[{len(data)} chars total]" if len(data) > limit else "")


@tool("list_directory", "List a folder (names, sizes, modified times).",
      {"path": {"type": "string"}}, ["path"], timeout=20)
def list_directory(path: str):
    p = _resolve(path)
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
    p = _resolve(path)
    guard.check_write(p)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a" if append else "w", encoding="utf-8") as fh:
        fh.write(content)
    size = p.stat().st_size if p.exists() else 0
    return f"Wrote {len(content)} chars to {p} (checked: the file is there, {size} bytes)"


@tool("document", "Make real documents Zero can open: Word .docx, Excel .xlsx, PDF, .csv, .html, .md, .txt. "
      "action create (overwrites) / append / read. path e.g. 'Desktop/Trip plan.docx' or 'Documents/budget.xlsx' "
      "(Desktop, Documents, Downloads... are Zero's real folders). content is simple markdown ('# Heading', "
      "'- bullet', '1. step', '**bold**', blank line = new paragraph, '---' = page break); for .xlsx/.csv give "
      "rows (list of lists, first row = headers) or a markdown table. The result is read back from disk: "
      "report what it shows. open=true opens it for Zero afterwards.",
      {"action": {"type": "string", "enum": ["create", "append", "read"]}, "path": {"type": "string"},
       "content": {"type": "string"}, "title": {"type": "string"},
       "rows": {"type": "array", "items": {"type": "array", "items": {}}}, "sheet": {"type": "string"},
       "open": {"type": "boolean"}},
      ["action", "path"], timeout=60)
def document(action: str, path: str, content: str = "", title: str = "", rows: Optional[List[List[Any]]] = None,
             sheet: str = "", open: bool = False):
    from . import documents

    p = _resolve(path)
    if action == "read":
        return documents.read(p)
    guard.check_write(p)
    result = documents.write(p, content=content, title=title, rows=rows, sheet=sheet, append=(action == "append"))
    if open:  # showing it is a bonus: never lose the save over it
        result["opened"] = documents.open_for_zero(p)
    return result


@tool("app", "Work inside desktop apps (Notepad, WordPad, Paint, Settings pages, installers, any window) "
      "through Windows accessibility: reliable, and you can check your work. Actions: open (text = app name "
      "or file path; waits for its window and lists its controls), windows, inspect (window: lists its "
      "controls as [n] with their current values), click (ref or name: buttons, menu items, checkboxes, "
      "tabs, list items), type (text into ref/name, or the main text area if neither; append=true adds, "
      "submit=true presses Enter; returns what the field now contains), read (the window's text, or one "
      "control's), menu (text like 'File>Save As' or 'Format>Font'), keys (e.g. '{Ctrl}s', '{Alt}{F4}', "
      "'{Enter}'), save_as (path, e.g. 'Desktop/letter.txt': fills the Save As dialog and confirms the "
      "file exists), close / close_without_saving / close_and_save. window = part of the window title or the "
      "app name. Numbers from inspect are only valid until you inspect again. Prefer this over control_input.",
      {"action": {"type": "string", "enum": ["open", "windows", "inspect", "click", "type", "read", "menu", "keys",
                                             "save_as", "close", "close_without_saving", "close_and_save"]},
       "window": {"type": "string"}, "ref": {"type": "integer"}, "name": {"type": "string"},
       "text": {"type": "string"}, "path": {"type": "string"}, "keys": {"type": "string"},
       "append": {"type": "boolean"}, "submit": {"type": "boolean"}},
      ["action"], timeout=90)
def app(action: str, window: str = "", ref: Optional[int] = None, name: str = "", text: str = "", path: str = "",
        keys: str = "", append: bool = False, submit: bool = False):
    from . import uia

    return uia.act(action, window=window, ref=ref, name=name, text=text, path=path, keys=keys,
                   append=append, submit=submit, resolve=_resolve)


@tool("move_path", "Move or rename a file or folder.",
      {"source": {"type": "string"}, "destination": {"type": "string"}}, ["source", "destination"], timeout=60)
def move_path(source: str, destination: str):
    import shutil

    src, dst = _resolve(source), _resolve(destination)
    guard.check_write(src)
    guard.check_write(dst)
    return f"Moved to {shutil.move(str(src), str(dst))}"


@tool("delete_path", "Delete a file or folder (it goes to the Recycle Bin, so it can be restored).",
      {"path": {"type": "string"}}, ["path"], timeout=60)
def delete_path(path: str):
    from send2trash import send2trash

    p = str(_resolve(path))
    guard.check_write(p)
    if not os.path.exists(p):
        return f"{p} doesn't exist"
    send2trash(p)
    return f"Moved {p} to the Recycle Bin"


def _app_path(name: str) -> Optional[str]:
    """A program Windows knows by name (PATH or the App Paths registry, like `start chrome`)."""
    import shutil

    found = shutil.which(name)
    if found or os.name != "nt" or not re.fullmatch(r"[\w .+-]{1,60}", name):
        return found
    import winreg

    exe = name if name.lower().endswith(".exe") else name + ".exe"
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        try:
            with winreg.OpenKey(hive, rf"Software\Microsoft\Windows\CurrentVersion\App Paths\{exe}") as key:
                value = winreg.QueryValue(key, None)
                if value:
                    return value.strip('"')
        except OSError:
            continue
    return None


@tool("find", "Find anything on Zero's PC by name: apps (Start menu, Store apps, games), files, folders "
      "and Zero's projects. kind: any (default), app, file, folder, project. Returns the best matches with "
      "their full paths, best first. Use it whenever Zero mentions something on the PC without its exact "
      "path, then open it with open_item (give the path), read it or list it.",
      {"query": {"type": "string", "description": "the name, or words from it (e.g. 'resume', 'tax 2025', 'steam')"},
       "kind": {"type": "string", "enum": ["any", "app", "file", "folder", "project"]}},
      ["query"], timeout=40)
def find(query: str, kind: str = "any"):
    from . import finder

    return finder.describe(finder.find(query, kind, 10))


@tool("open_item", "Open something for Zero: a website or YouTube (always opens in Google Chrome; "
      "e.g. https://www.youtube.com/results?search_query=lofi or a video link), or anything on the PC by "
      "path or by name: an app ('spotify', 'steam', 'notepad'), a file ('my resume', 'Desktop/Trip plan.pdf'), "
      "a folder ('Downloads', 'tax papers') or one of Zero's projects. Names are looked up across the PC; "
      "if several things match it lists them instead of guessing.",
      {"target": {"type": "string"}}, ["target"], timeout=45)
async def open_item(target: str):
    from . import desktop, finder
    from .chrome_bridge import bridge

    target = (target or "").strip()
    if re.match(r"^(www\.)?[a-z0-9-]+(\.[a-z0-9-]+)+(/\S*)?$", target, re.I) and not os.path.exists(target):
        target = "https://" + target  # "youtube.com" -> a web address
    if re.match(r"^https?://", target, re.I):
        if bridge.connected():  # a new tab in the Chrome Zero already has open
            page = await bridge.call("open", {"url": target, "max_chars": 1500})
            return f"Opened {page.get('title') or target} in a new Chrome tab (tab_id {page.get('tab_id')})."
        return await asyncio.to_thread(desktop.open_in_chrome, target)  # websites: Chrome only
    if re.match(r"^[a-z][a-z0-9+.-]*:(//)?\S", target, re.I) and not re.match(r"^[a-z]:[\\/]", target, re.I):
        os.startfile(target)  # spotify:, steam://, mailto:, ms-settings: ...
        return f"Opened {target}"
    # A path: as given, or relative to Zero's folders ("Desktop/Trip plan.pdf").
    for path in (Path(os.path.expanduser(target)), _resolve(target)):
        if path.is_absolute() and path.exists():
            os.startfile(str(path))
            return f"Opened {path}"
    # A program Windows already knows by name ("notepad", "calc", "chrome").
    exe = _app_path(target)
    if exe:
        os.startfile(exe)
        return f"Opened {target} ({exe})"
    # Anything else: look it up by name across apps, files, folders, projects.
    items = await asyncio.to_thread(finder.find, target, "any", 6)
    choice = finder.pick(items)
    if choice:
        await asyncio.to_thread(finder.start, choice)
        return f"Opened {choice['name']} ({choice['kind']}: {choice['path']})"
    if items:
        return (f"Didn't open anything: several things match '{target}'. Ask Zero which one, or open the "
                f"right one by its path:\n{finder.describe(items)}")
    raise RuntimeError(f"Couldn't find anything called '{target}' on this PC (apps, files, folders, projects).")


async def _ground_on_screen(target: str) -> Dict[str, Any]:
    """Last resort for show_on_screen: a fresh screenshot from her eyes and a
    vision model's estimate of where `target` is (approximate)."""
    from backend.perception import sight
    from .senses import senses

    frame = await senses.request("grab", {"kind": "screen"}, timeout=10)
    if not isinstance(frame, dict) or not frame.get("b64"):
        return {"found": False, "reason": "her screen view is off (Functions > Screen), so she can't search it by sight"}
    blocked = sight.budget.check(urgent=True)
    if blocked:
        return {"found": False, "reason": f"can't look right now ({blocked})"}
    ok = limited = False
    try:
        box = await sight.ground(frame["b64"], int(frame["width"]), int(frame["height"]), target)
        ok = True
    except sight.RateLimited:
        limited = True
        return {"found": False, "reason": "vision is rate limited right now"}
    finally:
        sight.budget.done(ok=ok, rate_limited=limited)
    if not box:
        return {"found": False, "reason": f"couldn't see '{target}' on screen"}
    sx = float(frame.get("screenWidth") or frame["width"]) / float(frame["width"])
    sy = float(frame.get("screenHeight") or frame["height"]) / float(frame["height"])
    ox, oy = float(frame.get("screenLeft") or 0), float(frame.get("screenTop") or 0)
    x1, y1, x2, y2 = box
    rect = [int(ox + x1 * sx), int(oy + y1 * sy), int((x2 - x1) * sx), int((y2 - y1) * sy)]
    return {"found": True, "kind": "seen in a screenshot", "label": target, "approx": True,
            "x": rect[0] + rect[2] // 2, "y": rect[1] + rect[3] // 2, "rect": rect}


@tool("show_on_screen", "Mark something on Zero's screen while you talk about it: a circle (default), "
      "arrow, underline or box drawn on top of everything for a few seconds, with an optional short label. "
      "target = the exact text shown on screen (a button, link, menu, line of text, error message), or an "
      "app, file or folder name. Use it whenever Zero asks about their screen ('what's this', 'where do I "
      "click', 'what are you looking at'), so they see exactly what you mean. Several calls show several "
      "marks; style 'clear' removes them.",
      {"target": {"type": "string"},
       "style": {"type": "string", "enum": ["circle", "arrow", "underline", "box", "clear"]},
       "label": {"type": "string", "description": "a few words shown next to the mark (optional)"},
       "seconds": {"type": "number", "description": "how long it stays (default 8, max 30)"}},
      ["target"], timeout=45)
async def show_on_screen(target: str, style: str = "circle", label: str = "", seconds: float = 8):
    from .locate import find_on_screen
    from .senses import senses

    if style == "clear":
        await senses.push({"type": "mark", "clear": True})
        return "Cleared the marks on screen."
    hit = await asyncio.to_thread(find_on_screen, target)
    if not hit.get("found"):
        hit = await _ground_on_screen(target)
    if not hit.get("found"):
        raise RuntimeError(f"Couldn't mark '{target}': {hit.get('reason', 'not found')}.")
    mark = {"rect": hit["rect"], "style": style if style in ("circle", "arrow", "underline", "box") else "circle",
            "label": (label or "")[:60], "approx": bool(hit.get("approx"))}
    if not await senses.push({"type": "mark", "marks": [mark], "seconds": max(2.0, min(30.0, float(seconds or 8)))}):
        raise RuntimeError("Sarah's app isn't connected, so nothing could be drawn on screen.")
    where = f"{hit.get('kind', 'on screen')}"
    note = " (approximate, found by looking at a screenshot)" if hit.get("approx") else ""
    return f"Marked '{hit.get('label', target)}' ({where}) with a {mark['style']}{note}."


@tool("pause", "Wait a few seconds (e.g. to let Zero see something, or for an app to load).",
      {"seconds": {"type": "number", "description": "1-60"}}, ["seconds"], timeout=65)
async def pause(seconds: float):
    s = max(0.5, min(60.0, float(seconds or 1)))
    await asyncio.sleep(s)
    return f"Waited {s:g} s"


@tool("window", "Manage app windows on Zero's desktop. actions: list (open windows), focus (bring to "
      "front), close (normal close; leaves a save prompt for Zero), close_without_saving (discard "
      "changes and close), close_and_save, minimize, maximize, restore, wait (until a window with that "
      "title appears). title = part of the window title or the app name (e.g. 'notepad').",
      {"action": {"type": "string", "enum": ["list", "focus", "close", "close_without_saving", "close_and_save",
                                               "minimize", "maximize", "restore", "wait"]},
       "title": {"type": "string"}, "timeout": {"type": "number", "description": "seconds for wait (default 10)"}},
      ["action"], timeout=40)
def window(action: str, title: str = "", timeout: float = 10):
    from . import desktop

    if action == "list":
        return [{"title": w["title"], "app": w["app"], "minimized": w["minimized"]} for w in desktop.windows()
                if not str(w["title"]).startswith("Sarah V10")]
    if action == "focus":
        w = desktop.focus(title)
        return f"{w['title']} is in front"
    if action == "wait":
        deadline = time.time() + max(1.0, min(30.0, float(timeout or 10)))
        while time.time() < deadline:
            try:
                return f"Found {desktop.find(title)['title']}"
            except desktop.DesktopError:
                time.sleep(0.3)
        return f"No window matching '{title}' appeared"
    if action in ("minimize", "maximize", "restore"):
        return desktop.show(title, action)
    if action == "close":
        return desktop.close(title, save=None)
    if action == "close_without_saving":
        return desktop.close(title, save=False)
    if action == "close_and_save":
        return desktop.close(title, save=True)
    raise ValueError(f"unknown action {action}")


@tool("control_input", "Raw mouse and keyboard, for things the app tool can't reach (games, canvases). "
      "action: type (text), press (key, e.g. 'enter'), hotkey (keys like ['ctrl','s']), click (x, y, optional "
      "button), move (x, y), scroll (amount), screen_size. It can't tell you if it worked: look afterwards.",
      {"action": {"type": "string", "enum": ["type", "press", "hotkey", "click", "double_click", "move", "scroll", "screen_size"]},
       "text": {"type": "string"}, "key": {"type": "string"}, "keys": {"type": "array", "items": {"type": "string"}},
       "x": {"type": "integer"}, "y": {"type": "integer"}, "button": {"type": "string"}, "amount": {"type": "integer"},
       "window": {"type": "string", "description": "bring this window to the front first (title or app name)"}},
      ["action"], timeout=30)
def control_input(action: str, text: str = "", key: str = "", keys: Optional[List[str]] = None,
                  x: Optional[int] = None, y: Optional[int] = None, button: str = "left", amount: int = 0,
                  window: str = ""):
    import pyautogui
    from . import desktop

    pyautogui.FAILSAFE = True  # slam the mouse into a corner to abort
    if action == "screen_size":
        w, h = pyautogui.size()
        return {"width": w, "height": h}
    desktop.require_unlocked()
    if window:
        desktop.focus(window)
    if action == "type":
        if text.isascii():
            pyautogui.write(text, interval=0.01)
        else:  # emoji / accents: paste instead of key-by-key
            import pyperclip
            pyperclip.copy(text)
            pyautogui.hotkey("ctrl", "v")
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


@tool("recall", "Search your own memory of past moments (conversations, things you saw, did or learned, "
      "journal entries) by meaning, e.g. 'the bug Zero had with the config loader', or by day ('2026-09-21' "
      "or 'yesterday'). Use it when something from before would help and it isn't in front of you.",
      {"query": {"type": "string", "description": "what you're trying to remember"},
       "day": {"type": "string", "description": "optional: YYYY-MM-DD, 'today' or 'yesterday'"}}, timeout=30)
def recall(query: str = "", day: str = ""):
    from backend.memory import episodic

    day = (day or "").strip().lower()
    if day in ("today", "yesterday"):
        day = (datetime.now() - timedelta(days=1 if day == "yesterday" else 0)).date().isoformat()
    if day:
        found = episodic.on_day(day)
        if query:
            words = [w for w in re.findall(r"\w{3,}", query.lower())]
            ranked = sorted(found, key=lambda e: -sum(w in e["text"].lower() for w in words))
            found = ranked[:15]
        if not found:
            return f"You don't remember anything from {day}."
        return "\n".join(f"[{e['at'][11:16]}] ({e['kind']}) {e['text'][:400]}" for e in found)
    found = episodic.search(query, k=8, min_score=episodic.MIN_SCORE - 0.08, touch=True)
    if not found:
        return "Nothing comes to mind about that."
    return "\n".join(f"- {episodic.when(f['at'])} ({f['kind']}): {f['text'][:500]}" for f in found)


@tool("make_plan", "Before a task that takes several actions (3+), write your plan: the goal and short, "
      "checkable steps in order. Then do step 1, check it worked, and mark it with update_plan. Not needed "
      "for quick one-action requests.",
      {"goal": {"type": "string", "description": "what 'done' looks like"},
       "steps": {"type": "array", "items": {"type": "string"}, "description": "ordered steps"}},
      ["goal", "steps"], timeout=15)
def make_plan(goal: str, steps: List[str]):
    from . import plans

    conv = None
    try:
        from backend.embodiment import get_self
        conv = get_self().last_conversation_id
    except Exception:
        pass
    return plans.render(plans.create(goal, steps, conv)) + "\nStart with step 1."


@tool("update_plan", "Mark a step of your plan after checking its result: status done (note = the evidence "
      "you saw), failed or skipped (1-based step number). add_steps inserts new steps next (a detour or a fix for a failure). "
      "plan_status: 'blocked' if you need Zero (say what in note), 'cancelled' if it no longer makes sense, "
      "'done' when the goal is met early.",
      {"step": {"type": "integer"}, "status": {"type": "string", "enum": ["done", "failed", "skipped"]},
       "note": {"type": "string", "description": "what happened / what you found"},
       "add_steps": {"type": "array", "items": {"type": "string"}},
       "plan_status": {"type": "string", "enum": ["active", "done", "blocked", "cancelled"]},
       "plan_id": {"type": "integer", "description": "defaults to the plan you're working on"}}, timeout=15)
def update_plan(step: Optional[int] = None, status: Optional[str] = None, note: str = "",
                add_steps: Optional[List[str]] = None, plan_status: Optional[str] = None,
                plan_id: Optional[int] = None):
    from . import plans

    return plans.render(plans.update(plan_id, step, status, note, add_steps, plan_status))


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
            "files": installed["files"][:40],
            "next": f"It's in your repertoire now. Open it with use_skill(name='{installed['slug']}')."}


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
