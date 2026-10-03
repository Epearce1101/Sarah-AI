"""Working with apps on Zero's desktop: windows, Chrome, typing.

Windows are found by (part of) their title. Closing "without saving" sends
the normal close request, answers the "save changes?" prompt with Don't
Save, and only if the app still won't close ends that one process. Sarah's
own windows and core Windows processes are never touched. When the PC is
locked, typing/clicking can't reach apps, so she's told that plainly.
"""
from __future__ import annotations

import ctypes
import json
import os
import re
import shutil
import subprocess
import time
from ctypes import wintypes
from pathlib import Path
from typing import Dict, List, Optional

user32 = ctypes.windll.user32 if os.name == "nt" else None
kernel32 = ctypes.windll.kernel32 if os.name == "nt" else None

WM_CLOSE = 0x0010
SW = {"minimize": 6, "maximize": 3, "restore": 9, "show": 5}
# Never closed or killed from here: Windows itself and Sarah.
PROTECTED = {"explorer.exe", "csrss.exe", "winlogon.exe", "services.exe", "lsass.exe", "svchost.exe",
             "dwm.exe", "smss.exe", "wininit.exe", "system", "taskmgr.exe", "logonui.exe",
             "electron.exe", "python.exe", "pythonw.exe", "sihost.exe", "startmenuexperiencehost.exe",
             "searchhost.exe", "shellexperiencehost.exe"}


class DesktopError(RuntimeError):
    pass


def is_locked() -> bool:
    """True when the lock screen is up (input can't reach apps)."""
    try:
        out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq LogonUI.exe", "/NH"], capture_output=True,
                             text=True, timeout=5, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return "LogonUI.exe" in out.stdout
    except Exception:
        return False


def require_unlocked() -> None:
    if is_locked():
        raise DesktopError("The PC is locked, so I can't type, click or switch windows. Zero needs to unlock it first.")


def _process_name(pid: int) -> str:
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    h = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(512)
        size = wintypes.DWORD(512)
        if kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
            return Path(buf.value).name.lower()
        return ""
    finally:
        kernel32.CloseHandle(h)


def windows() -> List[Dict[str, object]]:
    """Visible top-level windows with a title."""
    found: List[Dict[str, object]] = []
    EnumProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def cb(hwnd, _):
        if not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return True
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        found.append({"hwnd": int(hwnd), "title": buf.value, "pid": pid.value,
                      "app": _process_name(pid.value), "minimized": bool(user32.IsIconic(hwnd))})
        return True

    user32.EnumWindows(EnumProc(cb), 0)
    return [w for w in found if w["title"] not in ("Program Manager",)]


def find(title: str) -> Dict[str, object]:
    key = (title or "").strip().lower()
    if not key:
        raise DesktopError("give (part of) the window title or app name")
    from . import safety
    safety.check_not_guessing(title)
    matches = [w for w in windows() if key in str(w["title"]).lower() or key == str(w["app"]).removesuffix(".exe")]
    matches = [w for w in matches if not str(w["title"]).startswith("Sarah V10")]
    if not matches:
        raise DesktopError(f"No open window matches '{title}'.")
    exact = [w for w in matches if key in (str(w["title"]).lower(), str(w["app"]).removesuffix(".exe"))]
    if exact:
        return exact[0]
    if len({w["app"] for w in matches}) > 1:  # windows of different apps: ask, don't guess
        from . import safety
        raise safety.did_you_mean("window", title, [str(w["title"]) for w in matches][:4])
    return matches[0]


def focus(title: str) -> Dict[str, object]:
    require_unlocked()
    w = find(title)
    hwnd = w["hwnd"]
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, SW["restore"])
    # Windows only lets the foreground app hand over focus; a tap of Alt
    # counts as user input and unlocks SetForegroundWindow.
    user32.keybd_event(0x12, 0, 0, 0)
    user32.keybd_event(0x12, 0, 2, 0)
    user32.SetForegroundWindow(hwnd)
    time.sleep(0.25)
    return w


def show(title: str, how: str) -> str:
    w = find(title)
    user32.ShowWindow(w["hwnd"], SW[how])
    return f"{how}d {w['title']}"


def _alive(hwnd: int) -> bool:
    return bool(user32.IsWindow(hwnd)) and bool(user32.IsWindowVisible(hwnd))


def close(title: str, save: Optional[bool] = None, wait: float = 2.5) -> str:
    """Close a window. save=False answers a "save changes?" prompt with Don't
    Save (and ends the process if the app still won't close); save=None
    leaves any prompt for Zero."""
    w = find(title)
    if str(w["app"]) in PROTECTED:
        raise DesktopError(f"{w['app']} is part of Windows or Sarah; I won't close it.")
    hwnd, pid = w["hwnd"], int(w["pid"])
    user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)
    deadline = time.time() + wait
    while time.time() < deadline and _alive(hwnd):
        time.sleep(0.15)
    if not _alive(hwnd) and not [x for x in windows() if x["pid"] == pid and "save" in str(x["title"]).lower()]:
        return f"Closed {w['title']}"
    if save is None:
        return f"{w['title']} is asking about unsaved changes; left it for Zero to decide."
    require_unlocked()
    import pyautogui

    focus_target = next((x for x in windows() if x["pid"] == pid), None)
    if focus_target:
        focus(str(focus_target["title"]))
    # "Save" is Alt+S / Enter; "Don't save" is Alt+N in Windows apps.
    pyautogui.hotkey("alt", "s" if save else "n")
    deadline = time.time() + wait
    while time.time() < deadline and any(x["pid"] == pid for x in windows()):
        time.sleep(0.15)
    if not any(x["pid"] == pid for x in windows()):
        return f"Closed {w['title']} ({'saved' if save else 'without saving'})"
    if save:
        if any(x["pid"] == pid and "save as" in str(x["title"]).lower() for x in windows()):
            # A new document: Windows wants a name and folder. Not saved yet.
            import pyautogui
            pyautogui.press("esc")
            raise DesktopError(f"NOT saved: {w['title']} is a new file, so it asked where to save it. Use the app "
                               "tool: save_as with a path (e.g. Desktop/name.txt), then close the window.")
        raise DesktopError(f"NOT confirmed saved: {w['title']} is still open. Check it (app inspect) before saying it's saved.")
    subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True,
                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    return f"Closed {w['title']} without saving (the app had to be ended)"


_KNOWN_FOLDERS = {
    "desktop": "{B4BFCC3A-DB2C-424C-B029-7FE99A87C641}",
    "documents": "{FDD39AD0-238F-46AF-ADB4-6C85480369C7}",
    "downloads": "{374DE290-123F-4565-9164-39C4925E467B}",
    "pictures": "{33E28130-4E1E-4676-835A-98395C3BC3BB}",
    "music": "{4BD8D571-6D19-48D3-BE97-422220080E43}",
    "videos": "{18989B1D-99B5-455B-841C-AB7C74E4DDFC}",
}
_FOLDER_ALIASES = {"my documents": "documents", "docs": "documents", "my desktop": "desktop",
                   "download": "downloads", "photos": "pictures", "my pictures": "pictures"}


def known_folder(name: str) -> Optional[Path]:
    """Zero's real Desktop/Documents/... (wherever Windows keeps them, e.g. OneDrive)."""
    key = _FOLDER_ALIASES.get(name.strip().lower(), name.strip().lower())
    guid = _KNOWN_FOLDERS.get(key)
    if not guid:
        return None
    if os.name == "nt":
        class GUID(ctypes.Structure):
            _fields_ = [("d1", ctypes.c_ulong), ("d2", ctypes.c_ushort), ("d3", ctypes.c_ushort), ("d4", ctypes.c_ubyte * 8)]

        g = GUID()
        ctypes.windll.ole32.CLSIDFromString(ctypes.c_wchar_p(guid), ctypes.byref(g))
        out = ctypes.c_wchar_p()
        if ctypes.windll.shell32.SHGetKnownFolderPath(ctypes.byref(g), 0, None, ctypes.byref(out)) == 0:
            path = out.value
            ctypes.windll.ole32.CoTaskMemFree(out)
            if path:
                return Path(path)
    fallback = Path.home() / key.capitalize()
    return fallback if fallback.exists() else None


def chrome_path() -> Optional[str]:
    candidates = [
        Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Google/Chrome/Application/chrome.exe",
        Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "Google/Chrome/Application/chrome.exe",
        Path(os.environ.get("LOCALAPPDATA", "")) / "Google/Chrome/Application/chrome.exe",
    ]
    for c in candidates:
        if c.exists():
            return str(c)
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe") as k:
            value = winreg.QueryValue(k, None)
            if value and Path(value).exists():
                return value
    except OSError:
        pass
    return None


def open_in_chrome(url: str) -> str:
    chrome = chrome_path()
    if not chrome:
        raise DesktopError("Google Chrome isn't installed.")
    subprocess.Popen([chrome, url], creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    return f"Opened {url} in Chrome"


# ---------------------------------------------------------------------------
# Finding an app before starting it
# ---------------------------------------------------------------------------

def _start_menu_shortcuts() -> List[Path]:
    roots = [Path(os.environ.get("ProgramData", r"C:\ProgramData")), Path(os.environ.get("APPDATA", ""))]
    found: List[Path] = []
    for root in roots:
        menu = root / "Microsoft" / "Windows" / "Start Menu" / "Programs"
        if menu.is_dir():
            found += [p for p in menu.rglob("*.lnk") if "uninstall" not in p.stem.lower()]
    return found


def _store_apps() -> List[Dict[str, str]]:
    """Start menu apps that aren't shortcuts (Store apps), as {Name, AppID}."""
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command",
                              "Get-StartApps | Select-Object Name,AppID | ConvertTo-Json -Compress"],
                             capture_output=True, text=True, timeout=15,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout.strip()
        apps = json.loads(out) if out else []
        return [apps] if isinstance(apps, dict) else list(apps)
    except Exception:
        return []


def _plain(name: str) -> str:
    return " ".join(name.lower().removesuffix(".exe").split())


def find_app(name: str) -> Optional[str]:
    """What to launch for a file, app name ("spotify", "notepad", "Steam") or
    URI, or None if Windows wouldn't find it. Starting an unknown name with
    `start` pops up a "Windows cannot find..." box that stays on screen, so
    look first: an existing path, PATH, App Paths, Start menu shortcuts, then
    Store apps. Only an exact name is started; a near-match ("note",
    "spotfy") raises safety.AskZero so she asks "did you mean...?"."""
    from . import safety

    raw = (name or "").strip().strip('"')
    if not raw:
        return None
    safety.check_not_guessing(raw)
    t = os.path.expandvars(os.path.expanduser(raw))
    if os.path.exists(t):
        return t
    if re.match(r"^[a-z]:[\\/]", t, re.I) or "\\" in t or "/" in t:
        if re.match(r"^[a-z][a-z0-9+.\-]+:", t, re.I) and not re.match(r"^[a-z]:", t, re.I):
            return t  # a URI like steam://run/123: its handler opens it
        return None   # a path that isn't there
    if re.match(r"^[a-z][a-z0-9+.\-]+:", t, re.I):
        return t      # ms-settings:, spotify:, mailto:...
    for candidate in (t, t + ".exe"):
        hit = shutil.which(candidate)
        if hit:
            return hit
    exe = t if t.lower().endswith(".exe") else t + ".exe"
    try:
        import winreg
        for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
            try:
                with winreg.OpenKey(hive, rf"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\{exe}") as k:
                    value = (winreg.QueryValue(k, None) or "").strip('"')
                    if value and Path(value).exists():
                        return value
            except OSError:
                pass
    except ImportError:
        pass
    key = _plain(t)
    shortcuts = _start_menu_shortcuts()
    shortcut = next((p for p in shortcuts if _plain(p.stem) == key), None)
    if shortcut is not None:
        return str(shortcut)
    store = [a for a in _store_apps() if a.get("Name") and a.get("AppID")]
    app = next((a for a in store if _plain(a["Name"]) == key), None)
    if app is not None:
        return "shell:AppsFolder\\" + app["AppID"]
    near = safety.near_matches(key, [p.stem for p in shortcuts] + [a["Name"] for a in store])
    if near:
        raise safety.did_you_mean("app", raw, near)
    return None


def launch(target: str) -> str:
    """Start a file, app or URI the way double-clicking it would. Raises
    DesktopError (without any pop-up) when there's nothing by that name."""
    found = find_app(target)
    if not found:
        raise DesktopError(f"I couldn't find an app or file called '{target}' on this PC, so I didn't try to start "
                           "it (Windows would just show an error box). Ask Zero what it's called or where it is.")
    os.startfile(found)
    return found


# ---------------------------------------------------------------------------
# Screens and windows, for moving the pet out of the way (see pet.py)
# ---------------------------------------------------------------------------

class _RECT(ctypes.Structure):
    _fields_ = [("left", wintypes.LONG), ("top", wintypes.LONG), ("right", wintypes.LONG), ("bottom", wintypes.LONG)]


class _MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", _RECT), ("rcWork", _RECT), ("dwFlags", wintypes.DWORD)]


def _rect(r: _RECT):
    return (int(r.left), int(r.top), int(r.right), int(r.bottom))


_SHELL_CLASSES = {"Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd", "NotifyIconOverflowWindow",
                  "Windows.UI.Core.CoreWindow"}
WS_EX_TOOLWINDOW, WS_EX_NOACTIVATE = 0x80, 0x08000000


class _PhysicalPixels:
    """Real pixels on every monitor for this thread, whatever the scaling
    (Electron converts them back for the pet window)."""

    def __enter__(self):
        self.previous = None
        try:
            fn = user32.SetThreadDpiAwarenessContext
            fn.restype, fn.argtypes = ctypes.c_void_p, [ctypes.c_void_p]
            self.previous = fn(ctypes.c_void_p(-4))  # per-monitor aware v2
        except (AttributeError, OSError):
            pass
        return self

    def __exit__(self, *exc):
        if self.previous:
            user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(self.previous))


def _monitors() -> List[Dict[str, object]]:
    found: List[Dict[str, object]] = []
    Proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HMONITOR, wintypes.HDC, ctypes.POINTER(_RECT), wintypes.LPARAM)

    def cb(hmon, _hdc, _rc, _lp):
        info = _MONITORINFO()
        info.cbSize = ctypes.sizeof(_MONITORINFO)
        if not user32.GetMonitorInfoW(hmon, ctypes.byref(info)):
            return True
        scale = 1.0
        try:
            dx, dy = wintypes.UINT(), wintypes.UINT()
            if ctypes.windll.shcore.GetDpiForMonitor(hmon, 0, ctypes.byref(dx), ctypes.byref(dy)) == 0:
                scale = dx.value / 96
        except (AttributeError, OSError):
            pass
        found.append({"rect": _rect(info.rcMonitor), "work": _rect(info.rcWork),
                      "primary": bool(info.dwFlags & 1), "scale": scale})
        return True

    user32.EnumDisplayMonitors(None, None, Proc(cb), 0)
    return found


def _visible_rect(hwnd):
    """The window as drawn (without Windows 10's invisible resize borders)."""
    r = _RECT()
    try:
        if ctypes.windll.dwmapi.DwmGetWindowAttribute(hwnd, 9, ctypes.byref(r), ctypes.sizeof(r)) == 0:
            return _rect(r)
    except (AttributeError, OSError):
        pass
    user32.GetWindowRect(hwnd, ctypes.byref(r))
    return _rect(r)


def _cloaked(hwnd) -> bool:
    flag = wintypes.DWORD()
    try:
        return ctypes.windll.dwmapi.DwmGetWindowAttribute(hwnd, 14, ctypes.byref(flag), ctypes.sizeof(flag)) == 0 \
            and bool(flag.value)
    except (AttributeError, OSError):
        return False


def _app_windows() -> List[Dict[str, object]]:
    """Visible app windows, topmost first (not Sarah's, the taskbar, the
    desktop, overlays or hidden Store-app frames)."""
    found: List[Dict[str, object]] = []
    Proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    get_ex = getattr(user32, "GetWindowLongPtrW", user32.GetWindowLongW)

    def cb(hwnd, _):
        if not user32.IsWindowVisible(hwnd) or user32.IsIconic(hwnd) or _cloaked(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return True
        title = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, title, length + 1)
        cls = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, cls, 256)
        if cls.value in _SHELL_CLASSES or title.value.startswith("Sarah V10"):
            return True
        if get_ex(hwnd, -20) & (WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE):  # palettes, game overlays
            return True
        rect = _visible_rect(hwnd)
        if rect[2] - rect[0] < 50 or rect[3] - rect[1] < 50:
            return True
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        found.append({"hwnd": int(hwnd), "title": title.value, "app": _process_name(pid.value), "rect": rect})
        return True

    user32.EnumWindows(Proc(cb), 0)
    return found


def screen_layout() -> Dict[str, object]:
    """Monitors, app windows (topmost first) and the one Zero is using, in
    physical pixels."""
    if os.name != "nt":
        raise DesktopError("moving the pet needs Windows")
    with _PhysicalPixels():
        monitors = _monitors()
        wins = _app_windows()
        fg = int(user32.GetForegroundWindow() or 0)
    active = next((w for w in wins if w["hwnd"] == fg), wins[0] if wins else None)
    return {"monitors": monitors, "windows": wins, "active": active}
