"""Sarah's own agenda: things she has decided to do or follow up on.

She manages it herself by writing tags in anything she says (chat replies or
private moments of initiative):

    <agenda add="Ask how Zero's exam went" in="2d"/>   add (optionally due later)
    <agenda add="Wish Zero luck" at="2026-09-28T09:30"/>  ...or due at a local time
    <agenda done="3"/>                                  finish item 3 (or by text)

The tags are stripped from what Zero sees. The agenda is shown to her every
turn, and due items wake her mind loop.
"""
from __future__ import annotations

import json
import re
import threading
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from .guard import WORKSPACE

_FILE = WORKSPACE / "agenda.json"
_lock = threading.Lock()
_TAG = re.compile(
    r"<agenda\s+(add|done)\s*=\s*\"([^\"]{1,300})\"(?:\s+(in|at)\s*=\s*\"([^\"]{1,30})\")?\s*/?>", re.I)


def _load() -> List[Dict[str, Any]]:
    try:
        return json.loads(_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []


def _save(items: List[Dict[str, Any]]) -> None:
    _FILE.parent.mkdir(parents=True, exist_ok=True)
    _FILE.write_text(json.dumps(items[-200:], indent=1), encoding="utf-8")


def parse_duration(text: Optional[str]) -> Optional[timedelta]:
    m = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*(m|min|h|hr|d|day|days|w)\s*", (text or "").lower())
    if not m:
        return None
    n, unit = float(m.group(1)), m.group(2)
    return timedelta(minutes=n) if unit.startswith("m") else timedelta(hours=n) if unit.startswith("h") \
        else timedelta(weeks=n) if unit == "w" else timedelta(days=n)


def open_items() -> List[Dict[str, Any]]:
    with _lock:
        return [i for i in _load() if not i.get("done")]


def add(text: str, due_in: Optional[timedelta] = None) -> Dict[str, Any]:
    with _lock:
        items = _load()
        text = text.strip()
        for i in items:  # don't duplicate an open item
            if not i.get("done") and i["text"].lower() == text.lower():
                return i
        item = {"id": max([i["id"] for i in items] or [0]) + 1, "text": text,
                "created": datetime.now().isoformat(timespec="seconds"),
                "due": (datetime.now() + due_in).isoformat(timespec="seconds") if due_in else None,
                "done": False}
        items.append(item)
        _save(items)
        return item


def complete(ref: str) -> bool:
    with _lock:
        items = _load()
        ref = ref.strip().lstrip("#").strip()
        for i in items:
            if not i.get("done") and (str(i["id"]) == ref or i["text"].lower() == ref.lower()):
                i["done"] = True
                i["done_at"] = datetime.now().isoformat(timespec="seconds")
                _save(items)
                return True
    return False


def apply_tags(text: str) -> List[str]:
    """Apply every agenda tag in `text`; returns a log of what changed."""
    changes = []
    for verb, value, kind, when in _TAG.findall(text or ""):
        if verb.lower() == "add":
            due_in = parse_duration(when) if kind.lower() == "in" else None
            if kind.lower() == "at":
                try:  # an absolute local time, e.g. 2026-09-28T09:30
                    due_in = datetime.fromisoformat(when.strip()) - datetime.now()
                except ValueError:
                    due_in = None
            item = add(value, due_in)
            changes.append(f"added #{item['id']}")
        elif complete(value):
            changes.append(f"done {value}")
    return changes


def strip_tags(text: str) -> str:
    return _TAG.sub("", text or "")


def due_items(now: Optional[datetime] = None) -> List[Dict[str, Any]]:
    now = now or datetime.now()
    return [i for i in open_items() if i.get("due") and datetime.fromisoformat(i["due"]) <= now]


def render() -> str:
    items = open_items()
    if not items:
        return ""
    now = datetime.now()
    lines = []
    for i in items[-8:]:
        when = ""
        if i.get("due"):
            due = datetime.fromisoformat(i["due"])
            when = " (due now)" if due <= now else f" (due {due:%a %H:%M})"
        lines.append(f"  #{i['id']} {i['text']}{when}")
    return "- Your agenda (things you decided to do or follow up on):\n" + "\n".join(lines)
