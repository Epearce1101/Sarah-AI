"""Two brakes on Sarah's tools: Zero's OK before files are deleted or moved,
and a stop after repeated failures.

Approvals: delete_path, move_path and run_shell commands that delete or move
things don't act on the first call. They return an approval id and tell her
to ask Zero. The action runs when she calls again with that id, and only if
Zero has sent a message since that reads as a yes. She can't approve it
herself: the check is on Zero's own words, recorded by the chat endpoints.

The failure breaker: if tools fail ``agency_fail_limit`` times (5) within
``agency_fail_window_minutes`` (15), every tool is refused until Zero says
something (or presses Resume). When it trips, a screenshot is saved so Zero
can see what was on screen (an error box she couldn't get past, say), and
she is told to stop and say why instead of trying again.
"""
from __future__ import annotations

import re
import threading
import time
import uuid
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Any, Deque, Dict, List, Optional, Tuple

from backend.config import settings
from backend.identity import get_user_name

APPROVAL_TTL = 10 * 60

_lock = threading.Lock()


class NeedsApproval(PermissionError):
    """Not done: Zero has to OK this first. The message is shown to Sarah."""


# ---------------------------------------------------------------------------
# Zero's messages (recorded by api/chat.py)
# ---------------------------------------------------------------------------

_user = {"serial": 0, "text": "", "at": 0.0}


def note_user_message(text: str) -> None:
    """Zero said something: the answer to any pending question, and a fresh
    start after a stop."""
    with _lock:
        _user["serial"] += 1
        _user["text"] = text or ""
        _user["at"] = time.time()
    reset_failures()


_NO = re.compile(r"\b(no|nope|nah|don'?t|do not|stop|cancel|wait|hold on|not|never|leave it|keep (it|them|that))\b")
_YES = re.compile(r"\b(yes|yeah|yea|yep|yup|ya|sure|ok|okay|alright|all right|fine|go ahead|go for it|do it|"
                  r"please do|confirm(ed)?|approved?|delete (it|them|that)|move (it|them|that)|absolutely|of course)\b")


def reads_as_yes(text: str) -> Optional[bool]:
    """True for a clear yes, False for a no (or a hedge), None if unclear."""
    t = (text or "").lower().replace("’", "'")
    if _NO.search(t):
        return False
    if _YES.search(t):
        return True
    return None


# ---------------------------------------------------------------------------
# Approvals
# ---------------------------------------------------------------------------

_pending: Dict[str, Dict[str, Any]] = {}


def _key(args: Dict[str, Any]) -> str:
    return repr(sorted((k, str(v)) for k, v in args.items()))


def require_approval(tool: str, args: Dict[str, Any], what: str, approval_id: str = "") -> None:
    """Return if Zero has OK'd this exact action; otherwise raise NeedsApproval
    telling her what to ask."""
    user = get_user_name()
    now = time.time()
    with _lock:
        for pid in [p for p, v in _pending.items() if now - v["at"] > APPROVAL_TTL]:
            _pending.pop(pid, None)
        entry = _pending.get(approval_id) if approval_id else None
        if entry is not None and (entry["tool"] != tool or entry["key"] != _key(args)):
            entry = None  # an id for something else: treat as a new request
        if entry is None:
            pid = uuid.uuid4().hex[:8]
            _pending[pid] = {"tool": tool, "key": _key(args), "what": what, "at": now, "serial": _user["serial"]}
            raise NeedsApproval(
                f"NOT DONE YET: {user} has to OK this first. Ask {user} now, plainly: should you {what}? "
                f"Then stop and wait for their answer; don't do it another way. If they say yes, call "
                f"{tool} again with the same arguments and approval_id=\"{pid}\".")
        if _user["serial"] <= entry["serial"]:
            raise NeedsApproval(
                f"NOT DONE: {user} hasn't answered yet. Ask them whether you should {entry['what']}, "
                "then wait for their reply.")
        answer = reads_as_yes(_user["text"])
        if answer is None:
            raise NeedsApproval(
                f"NOT DONE: {user}'s reply wasn't a clear yes. Ask again whether you should {entry['what']} "
                f"(the same approval_id=\"{approval_id}\" works once they say yes).")
        _pending.pop(approval_id, None)
        if answer is False:
            raise NeedsApproval(f"NOT DONE: {user} said no. Leave it as it is and don't ask again.")


def pending_approvals() -> List[Dict[str, Any]]:
    with _lock:
        return [{"id": k, "tool": v["tool"], "what": v["what"]} for k, v in _pending.items()]


# Shell commands that delete or move things (run_shell asks first).
_SHELL_DELETE_OR_MOVE = re.compile(
    r"\b(remove-item|ri|rm|del|erase|rmdir|rd|move-item|mi|mv|move|rename-item|rni|ren|rename|"
    r"clear-recyclebin|robocopy\b.*/mov[e]?)\b|(\.|::)(delete|move|moveto)\s*\(", re.I)


def shell_deletes_or_moves(command: str) -> bool:
    return bool(_SHELL_DELETE_OR_MOVE.search(command or ""))


# ---------------------------------------------------------------------------
# The failure breaker
# ---------------------------------------------------------------------------

_failures: Deque[Tuple[float, str, str]] = deque()
_tripped: Optional[Dict[str, Any]] = None


def _limit() -> int:
    return max(1, int(getattr(settings, "agency_fail_limit", 5)))


def _window() -> float:
    return 60 * float(getattr(settings, "agency_fail_window_minutes", 15))


def tripped() -> Optional[Dict[str, Any]]:
    """Why she stopped (reason, snapshot, at), or None if she may carry on."""
    with _lock:
        return dict(_tripped) if _tripped else None


def reset_failures() -> None:
    global _tripped
    with _lock:
        _failures.clear()
        _tripped = None


def record_failure(tool: str, error: str) -> Optional[Dict[str, Any]]:
    """Count a failed tool call. Returns the stop record if this one trips
    the breaker (and only then)."""
    global _tripped
    now = time.time()
    with _lock:
        if _tripped:
            return None
        _failures.append((now, tool, " ".join((error or "").split())[:160]))
        while _failures and now - _failures[0][0] > _window():
            _failures.popleft()
        if len(_failures) < _limit():
            return None
        recent = list(_failures)
        _tripped = {"at": now, "failures": [{"tool": t, "error": e} for _, t, e in recent]}
    # Outside the lock: these touch the screen and can be slow.
    blockers = _error_windows()
    snapshot = _snapshot()
    last = recent[-1]
    reason = f"{len(recent)} tool calls failed within {int(_window() // 60)} minutes (last: {last[1]}: {last[2]})"
    if blockers:
        reason += f"; an error window is open: {', '.join(repr(b) for b in blockers[:3])}"
    with _lock:
        if _tripped is not None:
            _tripped.update({"reason": reason, "snapshot": snapshot, "error_windows": blockers})
            return dict(_tripped)
    return None


def stop_message(record: Dict[str, Any]) -> str:
    user = get_user_name()
    shot = f" A screenshot of the screen is saved at {record['snapshot']}." if record.get("snapshot") else ""
    return (f"STOPPED: {record.get('reason', 'too many failures')}.{shot} Your tools are paused until {user} "
            f"answers. Don't retry, and don't try to close error boxes yourself. Tell {user} in a sentence or "
            "two that you stopped and why" + (", and where the screenshot is" if shot else "") + ".")


def refused_message(record: Dict[str, Any]) -> str:
    return (f"Tools are paused: you stopped after repeated failures ({record.get('reason', '')}). "
            f"Don't retry; tell {get_user_name()} you stopped and why, and wait for them.")


def notice(record: Dict[str, Any]) -> str:
    """What she says if she'd otherwise stay quiet about it."""
    shot = f" I saved a screenshot of what was on screen: {record['snapshot']}" if record.get("snapshot") else ""
    return f"I stopped what I was doing because {record.get('reason', 'things kept failing')}.{shot}"


_ERROR_WORDS = ("error", "cannot find", "can't find", "not found", "warning", "problem", "failed",
                "not responding", "isn't responding", "stopped working", "exception")


def _error_windows() -> List[str]:
    try:
        from . import desktop
        return [str(w["title"]) for w in desktop.windows()
                if any(word in str(w["title"]).lower() for word in _ERROR_WORDS)][:5]
    except Exception:
        return []


def _snapshot() -> Optional[str]:
    try:
        from . import pc
        from .guard import WORKSPACE
        path = Path(WORKSPACE) / "snapshots" / f"stopped-{datetime.now():%Y%m%d-%H%M%S}.png"
        return pc.screenshot(path)["saved"]
    except Exception:
        return None
