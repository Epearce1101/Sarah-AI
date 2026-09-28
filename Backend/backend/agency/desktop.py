"""Working with apps on Zero's desktop: windows, Chrome, typing.

Windows are found by (part of) their title. Closing "without saving" sends
the normal close request, answers the "save changes?" prompt with Don't
Save, and only if the app still won't close ends that one process. Sarah's
own windows and core Windows processes are never touched. When the PC is
locked, typing/clicking can't reach apps, so she's told that plainly.
"""
from __future__ import annotations

import ctypes
import os
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
    matches = [w for w in windows() if key in str(w["title"]).lower() or key == str(w["app"]).removesuffix(".exe")]
    matches = [w for w in matches if not str(w["title"]).startswith("Sarah V10")]
    if not matches:
        raise DesktopError(f"No open window matches '{title}'.")
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
