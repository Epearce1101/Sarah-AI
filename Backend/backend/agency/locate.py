"""Where an app, file or folder Zero asked about is on screen.

Used by desktop pet mode so Sarah can point at the thing she's talking
about. A file or folder is found as the item itself (in an open Explorer
window or on the desktop), else a window showing it; an app as its window,
else its taskbar button, else a desktop shortcut. Coordinates are physical
screen pixels; the Electron main process turns them into the pet window's own.
"""
from __future__ import annotations

import ctypes
import os
import re
from typing import Dict, Iterable, List, Optional, Tuple

_URL = re.compile(r"^[a-z][a-z0-9+.-]*://", re.I)


def keys_for(target: str) -> Optional[Dict[str, str]]:
    """The names something could appear under on screen: full file name and
    name without extension (the desktop hides extensions), plus whether it
    looked like a path."""
    raw = (target or "").strip().strip('"').strip("'")
    if not raw or _URL.match(raw):
        return None
    is_path = bool(re.search(r"[\\/]", raw)) or bool(re.search(r"\.[A-Za-z0-9]{1,5}$", raw))
    name = re.split(r"[\\/]", raw.rstrip("\\/"))[-1].strip().lower()
    if not name or re.fullmatch(r"[a-z]:", name):
        return None
    stem = name.rsplit(".", 1)[0] if "." in name and is_path else name
    return {"name": name, "stem": stem, "path": "1" if is_path else ""}


def score(label: str, keys: Dict[str, str], app: str = "") -> int:
    """How well a window title / icon name matches: 3 exact, 2 as a whole word or the app, 0 none."""
    text = (label or "").strip().lower()
    name, stem = keys["name"], keys["stem"]
    if text and text in (name, stem):
        return 3
    if app and app.removesuffix(".exe") in (name, stem):
        return 2
    if not text:
        return 0
    # "report.pdf - Adobe Acrobat", "report - Word", "Google Chrome - 2 running windows"
    if re.search(rf"(^|[\s\-–—|:]){re.escape(name)}($|[\s\-–—|:])", text):
        return 2
    if len(stem) >= 3 and re.search(rf"(^|[\s\-–—|:]){re.escape(stem)}($|[\s\-–—|:.])", text):
        return 2
    return 0  # a name merely inside another ("Trip planner", "Chromecast") isn't it


def best(candidates: Iterable[Tuple[str, str, Tuple[int, int, int, int], str]], keys: Dict[str, str]
         ) -> Optional[Dict[str, object]]:
    """Pick the best-matching (label, kind, rect, app) candidate; earlier ones
    win ties (callers list them front-most first)."""
    top, found = 0, None
    for label, kind, rect, app in candidates:
        left, t, w, h = rect
        if w <= 0 or h <= 0 or left < -10000 or t < -10000:  # minimised / not on screen
            continue
        s = score(label, keys, app)
        if s > top:
            top, found = s, {"found": True, "kind": kind, "label": label,
                             "x": int(left + w / 2), "y": int(t + h / 2), "rect": [left, t, w, h]}
    return found


# ---------------------------------------------------------------------------
# Windows: what's on screen right now
# ---------------------------------------------------------------------------

def _dpi_aware() -> None:
    """Physical pixels for this thread (otherwise Windows scales rects)."""
    try:
        ctypes.windll.user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))  # per-monitor v2
    except Exception:
        pass


def _window_candidates() -> List[Tuple[str, str, Tuple[int, int, int, int], str]]:
    from ctypes import wintypes

    from . import desktop

    out = []
    for w in desktop.windows():  # front-most first
        title = str(w["title"])
        if w["minimized"] or title.startswith("Sarah"):
            continue
        r = wintypes.RECT()
        if not desktop.user32.GetWindowRect(w["hwnd"], ctypes.byref(r)):
            continue
        kind = "folder window" if w["app"] == "explorer.exe" else "window"
        out.append((title, kind, (r.left, r.top, r.right - r.left, r.bottom - r.top), str(w["app"])))
    return out


def _rect(ctrl) -> Tuple[int, int, int, int]:
    r = ctrl.BoundingRectangle
    return (r.left, r.top, r.right - r.left, r.bottom - r.top)


def _shell_candidates(keys: Dict[str, str]) -> List[Tuple[str, str, Tuple[int, int, int, int], str]]:
    """Desktop icons, items in open Explorer windows, taskbar buttons (UI Automation)."""
    from . import desktop, uia

    def collect():
        _dpi_aware()
        auto = uia._auto()
        out = []
        root = auto.GetRootControl()
        # Items showing in open folder windows (front-most first).
        for w in desktop.windows():
            if w["app"] != "explorer.exe" or w["minimized"]:
                continue
            try:
                win = auto.ControlFromHandle(w["hwnd"])
                view = win.ListControl(searchDepth=12, ClassName="UIItemsView") if win else None
                if view and view.Exists(0.2, 0):
                    for item in view.GetChildren()[:400]:
                        out.append((item.Name, f"item in {w['title']}", _rect(item), ""))
            except Exception:
                continue
        # Desktop icons (under Progman, or WorkerW with a slideshow wallpaper).
        for top in root.GetChildren():
            if top.ClassName not in ("Progman", "WorkerW"):
                continue
            try:
                icons = top.ListControl(searchDepth=3, ClassName="SysListView32")
                if icons.Exists(0.2, 0):
                    for item in icons.GetChildren()[:400]:
                        out.append((item.Name, "desktop icon", _rect(item), ""))
            except Exception:
                continue
        # Taskbar buttons ("Google Chrome - 2 running windows"): apps only.
        if not keys["path"]:
            try:
                bar = auto.PaneControl(searchDepth=1, ClassName="Shell_TrayWnd")
                if bar.Exists(0.2, 0):
                    def walk(ctrl, depth):
                        for child in ctrl.GetChildren():
                            if child.ControlTypeName == "ButtonControl" and child.Name:
                                out.append((child.Name.split(" - ")[0], "taskbar button", _rect(child), ""))
                            elif depth < 8:
                                walk(child, depth + 1)
                    walk(bar, 0)
            except Exception:
                pass
        return out

    return uia.run(collect)


def locate(target: str) -> Dict[str, object]:
    keys = keys_for(target)
    if not keys:
        return {"found": False, "reason": "nothing on screen to point at"}
    if os.name != "nt":
        return {"found": False, "reason": "only on Windows"}
    _dpi_aware()
    try:
        shell = _shell_candidates(keys)
    except Exception:
        shell = []
    windows = _window_candidates()
    items = [c for c in shell if c[1] != "taskbar button"]
    taskbar = [c for c in shell if c[1] == "taskbar button"]
    # A file or folder: the item itself, else a window showing it. An app:
    # its window, else its taskbar button, else a desktop shortcut.
    for group in ((items, windows) if keys["path"] else (windows, taskbar, items)):
        hit = best(group, keys)
        if hit:
            return hit
    return {"found": False, "reason": f"'{target}' isn't visible on screen"}


# ---------------------------------------------------------------------------
# Anything visible inside windows, for the screen overlay: buttons, links,
# text, list items, tabs... (UI Automation exposes most apps and browser pages)
# ---------------------------------------------------------------------------

_TEXTY = {"TextControl", "HyperlinkControl", "ButtonControl", "ListItemControl", "MenuItemControl",
          "TabItemControl", "TreeItemControl", "DataItemControl", "HeaderItemControl", "CheckBoxControl",
          "RadioButtonControl", "SplitButtonControl", "EditControl", "ImageControl", "ComboBoxControl"}


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s'.-]", " ", (text or "").lower())).strip()


def text_score(label: str, phrase: str) -> int:
    """How well visible text matches what she means: 3 the same, 2 contains
    the whole phrase, 1 has every one of its words, 0 none."""
    a, b = _norm(label), _norm(phrase)
    if not a or not b:
        return 0
    if a == b:
        return 3
    if len(b) >= 3 and re.search(rf"(^|\W){re.escape(b)}($|\W)", a):
        return 2
    words = re.findall(r"[a-z0-9']+", b)
    if words and all(re.search(rf"(^|\W){re.escape(w)}($|\W)", a) for w in words):
        return 1
    return 0


def best_text(candidates, phrase: str) -> Optional[Dict[str, object]]:
    """Best match: highest score, then the shortest text (the thing itself
    rather than a paragraph mentioning it), then front-most."""
    scored = []
    for order, (label, kind, rect, _app) in enumerate(candidates):
        left, t, w, h = rect
        if w <= 0 or h <= 0 or left < -10000 or t < -10000:
            continue
        s = text_score(label, phrase)
        if s:
            scored.append((-s, len(label), order, label, kind, rect))
    if not scored:
        return None
    s, _, _, label, kind, (left, t, w, h) = min(scored)
    return {"found": True, "kind": kind, "label": label, "score": -s,
            "x": int(left + w / 2), "y": int(t + h / 2), "rect": [left, t, w, h]}


def _ui_text_candidates(phrase: str, windows: int = 3, budget: float = 3.0):
    """Matching controls in the front-most windows (UI Automation)."""
    import time

    from . import desktop, uia

    def collect():
        _dpi_aware()
        auto = uia._auto()
        deadline = time.time() + budget
        out = []
        wins = [w for w in desktop.windows() if not w["minimized"] and not str(w["title"]).startswith("Sarah")]
        for w in wins[:windows]:
            try:
                top = auto.ControlFromHandle(w["hwnd"])
                for ctrl, _depth in auto.WalkControl(top, maxDepth=40):
                    if time.time() > deadline:
                        return out
                    if ctrl.ControlTypeName not in _TEXTY:
                        continue
                    name = ctrl.Name or ""
                    if not name or len(name) > 500 or not text_score(name, phrase):
                        continue
                    if getattr(ctrl, "IsOffscreen", False):  # scrolled out of view
                        continue
                    kind = ctrl.ControlTypeName.replace("Control", "").lower()
                    out.append((name, f"{kind} in {str(w['title'])[:60]}", _rect(ctrl), ""))
            except Exception:
                continue
        return out

    return uia.run(collect)


def find_on_screen(target: str) -> Dict[str, object]:
    """Exactly where `target` is on screen: visible text or a control in an
    open window, or an app / file / folder (see locate). Physical pixels."""
    phrase = (target or "").strip()
    if not phrase:
        return {"found": False, "reason": "nothing to find"}
    if os.name != "nt":
        return {"found": False, "reason": "only on Windows"}
    _dpi_aware()
    try:
        text_hit = best_text(_ui_text_candidates(phrase), phrase)
    except Exception:
        text_hit = None
    if text_hit and text_hit["score"] == 3:
        return text_hit
    thing = locate(phrase) if keys_for(phrase) else {"found": False}
    if thing.get("found"):
        return thing
    return text_hit or {"found": False, "reason": f"couldn't find '{target}' on screen"}
