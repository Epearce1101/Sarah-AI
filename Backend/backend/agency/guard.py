"""Guardrails for Sarah's actions on this PC.

Zero gave her full control, with one rule: system hardware and software are
not to be tampered with. This module enforces that in code (her prompt says
it too, but a prompt is not a guarantee):

- Protected locations can be read but never written, moved or deleted:
  Windows, Program Files, ProgramData, boot/recovery areas, and Sarah's own
  program files (everything under the install except her workspace).
- Shell commands that change the OS, drivers, boot, disks, services,
  security software, firewall, installed programs, power or user accounts
  are refused.
- Deleting always goes to the Recycle Bin (send2trash), never permanent.

These are guardrails against mistakes, not a sandbox against a hostile
program: her own code runs with the user's rights.
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Optional

from backend.config.settings import BACKEND_ROOT, REPO_ROOT

WORKSPACE = BACKEND_ROOT / "data" / "sarah_workspace"
TOOLS_DIR = WORKSPACE / "tools"


class Blocked(PermissionError):
    """An action the guardrails refuse. The message is shown to Sarah."""


def _norm(path: str | os.PathLike) -> str:
    p = Path(os.path.expandvars(os.path.expanduser(str(path))))
    try:
        p = p.resolve(strict=False)
    except OSError:
        p = p.absolute()
    return str(p).rstrip("\\/").lower()


def _system_roots() -> list[str]:
    windir = os.environ.get("SystemRoot") or os.environ.get("WINDIR") or r"C:\Windows"
    drive = os.environ.get("SystemDrive", "C:")
    roots = [
        windir,
        os.environ.get("ProgramFiles", rf"{drive}\Program Files"),
        os.environ.get("ProgramFiles(x86)", rf"{drive}\Program Files (x86)"),
        os.environ.get("ProgramW6432", rf"{drive}\Program Files"),
        os.environ.get("ProgramData", rf"{drive}\ProgramData"),
        rf"{drive}\Boot", rf"{drive}\Recovery", rf"{drive}\System Volume Information",
        rf"{drive}\$Recycle.Bin", rf"{drive}\EFI", rf"{drive}\PerfLogs",
        rf"{drive}\bootmgr", rf"{drive}\pagefile.sys", rf"{drive}\hiberfil.sys", rf"{drive}\swapfile.sys",
    ]
    return [_norm(r) for r in roots if r]


def _is_under(path: str, root: str) -> bool:
    return path == root or path.startswith(root + "\\")


def protected_reason(path: str | os.PathLike) -> Optional[str]:
    """Why `path` may not be changed, or None if it may."""
    p = _norm(path)
    if re.match(r"^[a-z]:$", p) or p in ("\\", ""):
        return "a whole drive root"
    for root in _system_roots():
        if _is_under(p, root):
            return f"system location ({root})"
    # Sarah's own program files: she may not break herself. Her workspace
    # (tools she writes, scratch files) is hers.
    install = _norm(REPO_ROOT)
    if _is_under(p, install) and not _is_under(p, _norm(WORKSPACE)):
        return "Sarah's own program files"
    return None


def check_write(path: str | os.PathLike) -> None:
    reason = protected_reason(path)
    if reason:
        raise Blocked(f"Refused: {path} is {reason}. System and program files are off limits.")


# Commands that change the machine itself rather than the user's things.
_BLOCKED_COMMANDS = [
    (r"\b(format|diskpart|bcdedit|bcdboot|bootrec|bootsect|mountvol|chkdsk\s+.*?/[fx])\b", "disks / boot"),
    (r"\b(format-volume|clear-disk|initialize-disk|remove-partition|set-partition|new-partition|resize-partition)\b", "disks / partitions"),
    (r"\breg(\.exe)?\s+(add|delete|import|restore|load|unload|copy)\b", "the registry"),
    (r"\b(set|new|remove|rename|clear)-item(property)?\b[^|;]*\b(hklm|hkey_local_machine|hkcr|hkey_classes_root|hku|hkey_users)\b", "the system registry"),
    (r"\b(sc(\.exe)?\s+(config|delete|stop|create|failure)|stop-service|set-service|remove-service|new-service|restart-service|net\s+stop|net\s+user|net\s+localgroup)\b", "services / accounts"),
    (r"\b(netsh\s+(advfirewall|firewall|interface|winsock)|set-netfirewall|new-netfirewallrule|remove-netfirewallrule|disable-netadapter)\b", "network / firewall"),
    (r"\b(set-mppreference|add-mppreference|remove-mppreference|uninstall-windowsfeature|disable-windowsoptionalfeature|enable-windowsoptionalfeature|dism(\.exe)?|sfc(\.exe)?)\b", "security software / Windows features"),
    (r"\b(msiexec(\.exe)?\s+/(x|uninstall)|uninstall-package|winget\s+uninstall|choco\s+uninstall|scoop\s+uninstall|wmic\s+.*\b(delete|uninstall|call)\b)", "installed software"),
    (r"\b(powercfg|bcdedit|set-executionpolicy|takeown|icacls|cacls|cipher\s+/w|vssadmin|wevtutil\s+cl|wbadmin|pnputil|devcon)\b", "system configuration / drivers"),
    (r"\b(shutdown(\.exe)?|stop-computer|restart-computer|logoff)\b", "shutting down / restarting the PC"),
    (r"\b(disable-computerrestore|checkpoint-computer|set-timezone|set-date|remove-localuser|disable-localuser|set-localuser)\b", "system settings / accounts"),
    (r"\bpip\s+uninstall\b|\bpython\S*\s+-m\s+pip\s+uninstall\b", "uninstalling Python packages (use her own workspace)"),
    (r"\b(rm|del|erase|rmdir|rd|remove-item|ri)\b[^|;&]*\s(/s|-r\b|-recurse)[^|;&]*\s[a-z]:\\?(\s|$|\")", "recursively deleting a drive"),
]


def check_command(command: str) -> None:
    text = (command or "").lower()
    for pattern, what in _BLOCKED_COMMANDS:
        if re.search(pattern, text):
            raise Blocked(f"Refused: this command would change {what}. System hardware and software are off limits.")
    # Paths the command writes to or removes (best effort: quoted or bare).
    if re.search(r"\b(remove-item|ri|rm|del|erase|rmdir|rd|move-item|mv|move|copy-item|cp|copy|set-content|out-file|new-item|rename-item|ren)\b|>", text):
        for candidate in re.findall(r"\"([a-z]:\\[^\"]*)\"|'([a-z]:\\[^']*)'|([a-z]:\\[^\s\"'|;&>]*)", text):
            path = next((c for c in candidate if c), "")
            if path and protected_reason(path):
                raise Blocked(f"Refused: {path} is {protected_reason(path)}. System and program files are off limits.")


def ensure_workspace() -> Path:
    TOOLS_DIR.mkdir(parents=True, exist_ok=True)
    return WORKSPACE
