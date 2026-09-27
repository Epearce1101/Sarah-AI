"""Identity loader package — read-only sync from OpenClaw `USER.md`.

Public surface:
    - load() / reload(): re-parse USER.md and replace the in-memory snapshot.
    - save_user_md(): write USER.md and reload the snapshot.
    - get_user(): return the current snapshot.
    - get_user_name(): return preferred form of address (call_name | name | fallback).
"""
from __future__ import annotations

from .loader import load, reload, parse_user_md, save_user_md
from .state import get_user, get_user_name, UserIdentity

__all__ = [
    "load",
    "reload",
    "save_user_md",
    "parse_user_md",
    "get_user",
    "get_user_name",
    "UserIdentity",
]
