"""Sarah inside desktop apps, through Windows UI Automation.

Like her browser, but for apps: a window is described as numbered controls
("[4] Button "Save"", "[7] Edit "File name:" = 'letter.txt'"), and she acts
on them by number or name: click, type (then reads the field back), read
text, open menus ("File>Save As..."), press keys. ``save_as`` drives the
standard Save As dialog to a real path and confirms the file exists.

Everything runs on one dedicated thread: UI Automation objects belong to the
COM apartment that created them, so the numbered controls from ``inspect``
stay valid for the next call.
"""
from __future__ import annotations

import concurrent.futures
import os
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import desktop

_executor = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="sarah-uia")
_refs: Dict[int, Any] = {}          # [n] -> control, from the last inspect
_refs_window: Optional[int] = None  # hwnd those numbers belong to
_initializer = None

INTERACTIVE = {"ButtonControl", "EditControl", "DocumentControl", "MenuItemControl", "CheckBoxControl",
               "RadioButtonControl", "ComboBoxControl", "ListItemControl", "TabItemControl", "HyperlinkControl",
               "TreeItemControl", "SplitButtonControl", "SliderControl", "SpinnerControl", "DataItemControl"}
INFORMATIVE = {"TextControl", "HeaderItemControl", "TitleBarControl", "StatusBarControl"}
MAX_CONTROLS = 180


def _auto():
    import uiautomation as auto

    global _initializer
    if _initializer is None:  # COM for this (single) thread, once
        _initializer = auto.UIAutomationInitializerInThread()
        auto.SetGlobalSearchTimeout(2)
    return auto


def run(fn, *args, **kwargs):
    """Run on the UI Automation thread (from any thread)."""
    return _executor.submit(fn, *args, **kwargs).result(timeout=120)


# ---------------------------------------------------------------------------
# Finding windows and controls
# ---------------------------------------------------------------------------

def _window(title: str):
    auto = _auto()
    w = desktop.find(title)
    if str(w["app"]) in desktop.PROTECTED and w["app"] != "explorer.exe":
        raise desktop.DesktopError(f"{w['app']} is off limits.")
    ctrl = auto.ControlFromHandle(w["hwnd"])
    if ctrl is None:
        raise desktop.DesktopError(f"Can't read the window '{w['title']}'.")
    return w, ctrl


def _pattern(ctrl, name: str):
    auto = _auto()
    try:
        return ctrl.GetPattern(getattr(auto.PatternId, name))
    except Exception:
        return None


def _value(ctrl) -> Optional[str]:
    vp = _pattern(ctrl, "ValuePattern")
    if vp is not None:
        try:
            return vp.Value
        except Exception:
            pass
    tp = _pattern(ctrl, "TextPattern")
    if tp is not None:
        try:
            return tp.DocumentRange.GetText(20000)
        except Exception:
            pass
    return None


def _toggle_state(ctrl) -> Optional[str]:
    tp = _pattern(ctrl, "TogglePattern")
    if tp is None:
        return None
    try:
        return {0: "off", 1: "on", 2: "mixed"}.get(int(tp.ToggleState))
    except Exception:
        return None


def _describe(n: int, ctrl) -> str:
    kind = ctrl.ControlTypeName.replace("Control", "")
    name = (ctrl.Name or "").strip().replace("\n", " ")[:70]
    line = f"[{n}] {kind}" + (f' "{name}"' if name else "")
    if kind in ("Edit", "Document", "ComboBox", "Spinner"):
        v = _value(ctrl)
        if v:
            v = v.replace("\r", "").replace("\n", "\\n")
            line += f" = '{v[:80]}{'...' if len(v) > 80 else ''}'"
    state = _toggle_state(ctrl)
    if state:
        line += f" ({state})"
    if not ctrl.IsEnabled:
        line += " (disabled)"
    return line


def _walk(root) -> List[Any]:
    found: List[Any] = []
    stack = [(root, 0)]
    while stack and len(found) < MAX_CONTROLS:
        ctrl, depth = stack.pop()
        try:
            children = ctrl.GetChildren()
        except Exception:
            children = []
        for child in reversed(children):
            stack.append((child, depth + 1))
        if ctrl is root or depth > 14:
            continue
        try:
            if ctrl.IsOffscreen:
                continue
            kind = ctrl.ControlTypeName
        except Exception:
            continue
        if kind in INTERACTIVE or (kind in INFORMATIVE and (ctrl.Name or "").strip()):
            found.append(ctrl)
    return found


def _inspect(title: str) -> Dict[str, Any]:
    global _refs, _refs_window
    w, root = _window(title)
    controls = _walk(root)
    _refs = {i: c for i, c in enumerate(controls, 1)}
    _refs_window = w["hwnd"]
    return {"window": w["title"], "app": w["app"], "controls": [_describe(i, c) for i, c in _refs.items()]}


def _target(title: str, ref: Optional[int], name: str):
    w, root = _window(title)
    if ref is not None:
        if _refs_window != w["hwnd"] or int(ref) not in _refs:
            raise ValueError("those numbers are from another window or out of date: inspect this window again")
        return w, _refs[int(ref)]
    want = (name or "").strip().lower()
    if not want:
        raise ValueError("give ref (the [n] number from inspect) or name")
    exact, partial = None, None
    for c in _walk(root):
        cname = (c.Name or "").strip().lower()
        if cname == want and exact is None:
            exact = c
        elif want in cname and partial is None:
            partial = c
    ctrl = exact or partial
    if ctrl is None:
        raise ValueError(f"no control named '{name}' in {w['title']}: inspect it to see what's there")
    return w, ctrl


def _new_windows(before: List[Dict[str, Any]]) -> List[str]:
    old = {x["hwnd"] for x in before}
    return [str(x["title"]) for x in desktop.windows() if x["hwnd"] not in old]


# ---------------------------------------------------------------------------
# Actions
# ---------------------------------------------------------------------------

def _click(title: str, ref: Optional[int], name: str) -> Dict[str, Any]:
    desktop.require_unlocked()
    before = desktop.windows()
    w, ctrl = _target(title, ref, name)
    label = _describe(0, ctrl)[4:]
    done = None
    for pattern, call in (("InvokePattern", "Invoke"), ("TogglePattern", "Toggle"),
                          ("SelectionItemPattern", "Select"), ("ExpandCollapsePattern", "Expand")):
        p = _pattern(ctrl, pattern)
        if p is not None:
            try:
                getattr(p, call)()
                done = call.lower()
                break
            except Exception:
                continue
    if done is None:
        desktop.focus(str(w["title"]))
        ctrl.Click(simulateMove=False)
        done = "clicked"
    time.sleep(0.6)
    out: Dict[str, Any] = {"did": f"{done} {label}"}
    opened = _new_windows(before)
    if opened:
        out["new_windows"] = opened
    state = _toggle_state(ctrl) if ctrl.Exists(0, 0) else None
    if state:
        out["now"] = state
    return out


def _type(title: str, text: str, ref: Optional[int], name: str, append: bool, submit: bool) -> Dict[str, Any]:
    desktop.require_unlocked()
    auto = _auto()
    if ref is None and not name:
        # The main text area: the first Document/Edit in the window.
        _w, root = _window(title)
        ctrl = next((c for c in _walk(root) if c.ControlTypeName in ("DocumentControl", "EditControl")), None)
        if ctrl is None:
            raise ValueError("no text box found: inspect the window and give ref")
        w = _w
    else:
        w, ctrl = _target(title, ref, name)
    vp = _pattern(ctrl, "ValuePattern")
    how = None
    if vp is not None and not append:
        try:
            if not vp.IsReadOnly:
                vp.SetValue(text)
                how = "set"
        except Exception:
            how = None
    if how is None:  # type it for real
        desktop.focus(str(w["title"]))
        ctrl.SetFocus()
        if not append:
            auto.SendKeys("{Ctrl}a{Delete}", waitTime=0.05)
        auto.SendKeys(_escape_keys(text), interval=0.005, waitTime=0.1)
        how = "typed"
    if submit:
        desktop.focus(str(w["title"]))
        auto.SendKeys("{Enter}", waitTime=0.3)
    time.sleep(0.3)
    now = _value(ctrl) if ctrl.Exists(0, 0) else None
    out: Dict[str, Any] = {"did": f"{how} text in {_describe(0, ctrl)[4:].split(' = ')[0]}"}
    if now is not None:
        norm = lambda s: re.sub(r"\s+", " ", s or "").strip()
        out["field_now_contains"] = now[:1500]
        out["check"] = "matches" if norm(text) in norm(now) else "DOES NOT MATCH what you meant to type: fix it"
    return out


def _escape_keys(text: str) -> str:
    """Literal text for uiautomation.SendKeys ({...} are key names there)."""
    special = {"{": "{{}", "}": "{}}", "\n": "{Enter}", "\t": "{Tab}"}
    return "".join(special.get(ch, ch) for ch in text.replace("\r\n", "\n").replace("\r", "\n"))


def _read(title: str, ref: Optional[int], name: str) -> Dict[str, Any]:
    if ref is not None or name:
        w, ctrl = _target(title, ref, name)
        return {"window": w["title"], "control": _describe(0, ctrl)[4:].split(" = ")[0], "text": (_value(ctrl) or ctrl.Name or "")[:20000]}
    w, root = _window(title)
    texts = []
    for c in _walk(root):
        if c.ControlTypeName in ("DocumentControl", "EditControl"):
            v = _value(c)
            if v:
                texts.append(v)
        elif c.ControlTypeName == "TextControl" and c.Name:
            texts.append(c.Name)
    return {"window": w["title"], "text": "\n".join(texts)[:20000]}


def _menu(title: str, path: str) -> Dict[str, Any]:
    desktop.require_unlocked()
    auto = _auto()
    before = desktop.windows()
    w, root = _window(title)
    desktop.focus(str(w["title"]))
    steps = [s.strip() for s in re.split(r">|/|->", path or "") if s.strip()]
    if not steps:
        raise ValueError("give a menu path like 'File>Save As'")
    scope = root
    for i, step in enumerate(steps):
        want = step.lower().rstrip(".")
        item = None
        deadline = time.time() + 3
        while item is None and time.time() < deadline:
            # Open menus are separate popup windows: search the whole desktop after the first step.
            search_root = scope if i == 0 else auto.GetRootControl()
            for c in _menu_items(search_root, depth=6 if i else 10):
                if (c.Name or "").lower().replace("&", "").rstrip(".").startswith(want):
                    item = c
                    break
            if item is None:
                time.sleep(0.2)
        if item is None:
            auto.SendKeys("{Esc}{Esc}", waitTime=0.1)
            raise ValueError(f"no menu item '{step}' (at step {i + 1} of {path})")
        ec = _pattern(item, "ExpandCollapsePattern")
        ip = _pattern(item, "InvokePattern")
        if i < len(steps) - 1 and ec is not None:
            ec.Expand()
        elif ip is not None:
            ip.Invoke()
        else:
            item.Click(simulateMove=False)
        time.sleep(0.4)
    out: Dict[str, Any] = {"did": f"chose {' > '.join(steps)}"}
    opened = _new_windows(before)
    if opened:
        out["new_windows"] = opened
    return out


def _menu_items(root, depth: int) -> List[Any]:
    items, stack = [], [(root, 0)]
    while stack:
        c, d = stack.pop()
        try:
            if c.ControlTypeName == "MenuItemControl" and not c.IsOffscreen:
                items.append(c)
            if d < depth:
                stack.extend((ch, d + 1) for ch in c.GetChildren())
        except Exception:
            continue
    return items


def _keys(title: str, keys: str) -> Dict[str, Any]:
    desktop.require_unlocked()
    auto = _auto()
    before = desktop.windows()
    desktop.focus(title)
    auto.SendKeys(keys, waitTime=0.3)
    out: Dict[str, Any] = {"did": f"pressed {keys}"}
    opened = _new_windows(before)
    if opened:
        out["new_windows"] = opened
    return out


def _find_dialog(app_pid: int, words: tuple, timeout: float = 6.0) -> Optional[Dict[str, Any]]:
    deadline = time.time() + timeout
    while time.time() < deadline:
        for x in desktop.windows():
            title = str(x["title"]).lower()
            if x["pid"] == app_pid and any(wd in title for wd in words):
                return x
        time.sleep(0.2)
    return None


def _save_as(title: str, path: Path) -> Dict[str, Any]:
    """File > Save As to a real path, answering "replace?", then prove it."""
    desktop.require_unlocked()
    auto = _auto()
    w, root = _window(title)
    pid = int(w["pid"])
    started = time.time()
    path.parent.mkdir(parents=True, exist_ok=True)
    desktop.focus(str(w["title"]))
    dialog = None
    try:
        _menu(str(w["title"]), "File>Save As")
        dialog = _find_dialog(pid, ("save as", "save"), 4)
    except Exception:
        pass
    if dialog is None:
        for keys in ("{Ctrl}{Shift}s", "{F12}", "{Ctrl}s"):
            desktop.focus(str(w["title"]))
            auto.SendKeys(keys, waitTime=0.2)
            dialog = _find_dialog(pid, ("save as", "save"), 3)
            if dialog:
                break
    if dialog is None:
        raise desktop.DesktopError(f"No Save As dialog appeared in {w['title']}.")
    dlg = auto.ControlFromHandle(dialog["hwnd"])
    name_box = None
    for c in _walk(dlg):
        if c.ControlTypeName == "EditControl" and (c.AutomationId == "1001" or "file name" in (c.Name or "").lower()):
            name_box = c
            break
    if name_box is None:
        raise desktop.DesktopError("Found the Save As dialog but not its File name box.")
    vp = _pattern(name_box, "ValuePattern")
    if vp is not None:
        vp.SetValue(str(path))
    else:
        name_box.SetFocus()
        auto.SendKeys("{Ctrl}a" + _escape_keys(str(path)), waitTime=0.1)
    # Plain text only for .txt in Notepad-like apps: pick the matching "Save as type" when it's offered.
    save_btn = next((c for c in _walk(dlg) if c.ControlTypeName == "ButtonControl"
                     and (c.AutomationId == "1" or (c.Name or "").lower() in ("save", "&save"))), None)
    if save_btn is not None and _pattern(save_btn, "InvokePattern") is not None:
        _pattern(save_btn, "InvokePattern").Invoke()
    else:
        desktop.focus(str(dialog["title"]))
        auto.SendKeys("{Enter}", waitTime=0.2)
    # Follow-up prompts ("already exists, replace it?", "keep text format?"):
    # any other window of the app that isn't the main one or the Save As dialog.
    app_name = str(w["app"]).removesuffix(".exe").lower()
    deadline = time.time() + 5
    while time.time() < deadline:
        if path.exists() and path.stat().st_mtime >= started - 2:
            break
        prompt = next((x for x in desktop.windows() if x["pid"] == pid and x["hwnd"] not in (w["hwnd"], dialog["hwnd"])
                       and any(k in str(x["title"]).lower() for k in ("confirm", "replace", app_name, "save"))), None)
        if prompt is not None:
            pc = auto.ControlFromHandle(prompt["hwnd"])
            yes = next((c for c in _walk(pc) if c.ControlTypeName == "ButtonControl" and (c.Name or "").lower()
                        .replace("&", "") in ("yes", "keep text format", "save", "ok")), None)
            if yes is not None and _pattern(yes, "InvokePattern") is not None:
                _pattern(yes, "InvokePattern").Invoke()
        time.sleep(0.3)
    # Proof: the file is there and was just written.
    if not path.exists() or path.stat().st_mtime < started - 2:
        leftover = _find_dialog(pid, ("save as",), 0.5)
        raise desktop.DesktopError(f"Saving didn't produce {path}" + (" (the Save As dialog is still open)" if leftover else ""))
    titles = [str(x["title"]) for x in desktop.windows() if x["pid"] == pid]
    return {"saved": str(path), "bytes": path.stat().st_size, "app_window_now": titles[0] if titles else None,
            "check": "the file exists and was just written"}


def _open(target: str, wait: float = 15.0) -> Dict[str, Any]:
    import subprocess

    before = desktop.windows()
    old = {x["hwnd"] for x in before}
    t = os.path.expanduser(target.strip().strip('"'))
    if os.path.exists(t):
        os.startfile(t)
    else:
        subprocess.Popen(["cmd", "/c", "start", "", t], creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    key = Path(t).stem.lower()
    deadline = time.time() + wait
    while time.time() < deadline:
        fresh = [x for x in desktop.windows() if x["hwnd"] not in old and not str(x["title"]).startswith("Sarah V10")]
        match = next((x for x in fresh if key in str(x["title"]).lower() or key == str(x["app"]).removesuffix(".exe")),
                     fresh[0] if fresh else None)
        if match:
            time.sleep(0.8)  # let it finish drawing
            return {"opened": match["title"], **_inspect(str(match["title"]))}
        time.sleep(0.3)
    return {"opened": None, "note": f"started {target}, but no new window appeared within {int(wait)} s"}


# Public entry points (called from the app tool, on any thread) --------------

def act(action: str, window: str = "", ref: Optional[int] = None, name: str = "", text: str = "",
        path: str = "", keys: str = "", append: bool = False, submit: bool = False,
        resolve=None) -> Dict[str, Any]:
    if action == "open":
        return run(_open, text or window or name)
    if action == "windows":
        return {"windows": [f"{x['title']} ({x['app']})" for x in desktop.windows()
                            if not str(x["title"]).startswith("Sarah V10")]}
    if not window:
        raise ValueError("give window (part of its title or the app name)")
    if action == "inspect":
        return run(_inspect, window)
    if action == "click":
        return run(_click, window, ref, name)
    if action == "type":
        return run(_type, window, text, ref, name, append, submit)
    if action == "read":
        return run(_read, window, ref, name)
    if action == "menu":
        return run(_menu, window, text or name)
    if action == "keys":
        return run(_keys, window, keys or text)
    if action in ("close", "close_without_saving", "close_and_save"):
        save = {"close": None, "close_without_saving": False, "close_and_save": True}[action]
        return {"did": desktop.close(window, save=save)}
    if action == "save_as":
        if not path:
            raise ValueError("give path, e.g. Desktop/letter.txt")
        target = resolve(path) if resolve else Path(path)
        from . import guard
        guard.check_write(target)
        return run(_save_as, window, target)
    raise ValueError(f"unknown app action {action!r}")
