"""Zero's standing notes for Sarah: how to behave, things to always remember.

Written in the Functions tab. Stored in the settings table and read on every
request (ContextBuilder, the multi-agent path), so a save applies to every
conversation, typed or spoken, from her next reply on.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, Optional

from backend.models.core import get_setting, set_setting

NOTES_KEY = "user_notes"
UPDATED_KEY = "user_notes_updated_at"
MAX_CHARS = 8000


def get_notes() -> Dict[str, Optional[str]]:
    try:
        text = get_setting(NOTES_KEY) or ""
        updated = get_setting(UPDATED_KEY)
    except Exception:
        text, updated = "", None
    return {"notes": text, "updated_at": updated}


def save_notes(text: str) -> Dict[str, Optional[str]]:
    clean = (text or "").replace("\r\n", "\n").strip()
    if len(clean) > MAX_CHARS:
        raise ValueError(f"Notes are limited to {MAX_CHARS} characters.")
    set_setting(NOTES_KEY, clean)
    set_setting(UPDATED_KEY, datetime.now(timezone.utc).isoformat(timespec="seconds"))
    return get_notes()


def build_notes_block() -> str:
    """The prompt block for her system message; empty when there are no notes."""
    text = get_notes()["notes"]
    if not text:
        return ""
    return (
        "# Zero's notes for you\n"
        "Zero wrote these standing notes in your settings. They hold in every "
        "conversation: follow the instructions about how to behave, and treat "
        "the rest as things you know and keep in mind. They take priority over "
        "your default habits and style (your safety limits still apply). Use "
        "them naturally; don't quote them back unless asked.\n"
        "<notes>\n" + text + "\n</notes>"
    )
