"""In-memory identity snapshot, guarded by a lock.

The snapshot is immutable (frozen dataclass). `_user` is replaced atomically
by the loader; readers always see a complete snapshot.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from backend.config.settings import settings


@dataclass(frozen=True)
class UserIdentity:
    """Parsed snapshot of `~/.openclaw/workspace/USER.md`."""

    name: str = ""
    call_name: str = ""
    pronouns: str = ""
    timezone: str = ""
    notes: str = ""
    source_path: Optional[Path] = None
    loaded_at: float = 0.0


_user: UserIdentity = UserIdentity()
_lock = threading.Lock()


def set_user(snapshot: UserIdentity) -> None:
    """Atomically replace the in-memory snapshot. Called by the loader."""
    global _user
    with _lock:
        _user = snapshot


def get_user() -> UserIdentity:
    """Return the current snapshot (frozen — safe to share)."""
    with _lock:
        return _user


def get_user_name() -> str:
    """Return preferred form of address.

    Resolution order: `call_name` (preferred form), then `name` (legal/full),
    then the configured fallback (`Creator` by default). The OpenClaw USER.md
    template separates these intentionally — `call_name` is what callers
    should use to address the user.
    """
    snapshot = get_user()
    return (
        snapshot.call_name
        or snapshot.name
        or settings.user_display_name_fallback
    )
