"""Everyday PC controls (the useful bits of the "Jarvis" assistants):
volume, media keys, clipboard, screenshots and how the PC is doing.

Nothing here changes system settings: volume/media are the same keys and
mixer Zero uses; stats are read-only.
"""
from __future__ import annotations

import ctypes
import os
import subprocess
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Optional

VK = {"play_pause": 0xB3, "next": 0xB0, "previous": 0xB1, "stop": 0xB2}


def _volume_iface():
    import comtypes
    from pycaw.pycaw import AudioUtilities

    try:
        comtypes.CoInitialize()
    except OSError:
        pass
    return AudioUtilities.GetSpeakers().EndpointVolume


def volume(level: Optional[float] = None, change: Optional[float] = None, mute: Optional[bool] = None) -> Dict[str, Any]:
    """Set (0-100), nudge (+/-) or (un)mute the speakers; returns what it is now."""
    v = _volume_iface()
    if level is not None:
        v.SetMasterVolumeLevelScalar(max(0.0, min(100.0, float(level))) / 100, None)
    if change is not None:
        now = v.GetMasterVolumeLevelScalar() * 100
        v.SetMasterVolumeLevelScalar(max(0.0, min(100.0, now + float(change))) / 100, None)
    if mute is not None:
        v.SetMute(1 if mute else 0, None)
    return {"volume": round(v.GetMasterVolumeLevelScalar() * 100), "muted": bool(v.GetMute())}


def media(key: str) -> str:
    code = VK.get(key)
    if code is None:
        raise ValueError(f"media key must be one of {', '.join(VK)}")
    user32 = ctypes.windll.user32
    user32.keybd_event(code, 0, 0, 0)
    user32.keybd_event(code, 0, 2, 0)
    return f"pressed {key.replace('_', '/')}"


def clipboard_read() -> str:
    import pyperclip
    return pyperclip.paste() or ""


def clipboard_write(text: str) -> str:
    import pyperclip
    pyperclip.copy(text or "")
    return f"copied {len(text or '')} characters (checked: {'ok' if pyperclip.paste() == (text or '') else 'MISMATCH'})"


def screenshot(path: Path) -> Dict[str, Any]:
    import pyautogui
    path.parent.mkdir(parents=True, exist_ok=True)
    img = pyautogui.screenshot()
    img.save(str(path))
    return {"saved": str(path), "bytes": path.stat().st_size, "size": f"{img.width}x{img.height}"}


def _gpu() -> Optional[Dict[str, Any]]:
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,utilization.gpu,memory.used,memory.total,temperature.gpu",
             "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=5,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout.strip().splitlines()
        name, util, used, total, temp = [x.strip() for x in out[0].split(",")]
        return {"name": name, "busy_percent": int(util), "memory_mb": f"{used}/{total}", "temp_c": int(temp)}
    except Exception:
        return None


def stats() -> Dict[str, Any]:
    import psutil

    mem = psutil.virtual_memory()
    disks = {}
    for part in psutil.disk_partitions(all=False):
        try:
            u = psutil.disk_usage(part.mountpoint)
            disks[part.device.rstrip("\\")] = f"{u.free // 2**30} GB free of {u.total // 2**30} GB"
        except (PermissionError, OSError):
            continue
    procs = []
    for p in psutil.process_iter(["name", "memory_info"]):
        try:
            procs.append((p.info["memory_info"].rss, p.info["name"]))
        except Exception:
            continue
    top = [f"{name} ({rss // 2**20} MB)" for rss, name in sorted(procs, reverse=True)[:6]]
    battery = psutil.sensors_battery()
    boot = datetime.fromtimestamp(psutil.boot_time())
    return {
        "cpu_percent": psutil.cpu_percent(interval=0.5),
        "ram": f"{mem.used // 2**20} MB used of {mem.total // 2**20} MB ({mem.percent}%)",
        "gpu": _gpu(),
        "disks": disks,
        "battery": f"{battery.percent}%{' charging' if battery.power_plugged else ''}" if battery else "no battery (desktop)",
        "up_for": str(timedelta(seconds=int(time.time() - boot.timestamp()))),
        "biggest_programs": top,
    }
