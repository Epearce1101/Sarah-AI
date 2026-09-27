"""Persona loader package — read-only sync from OpenClaw `IDENTITY.md`/`SOUL.md`.

Public surface:
    - load() / reload(): re-parse persona files, replace the in-memory snapshot.
    - switch(): write active persona state, then reload.
    - get_persona(): return the current snapshot.
    - build_persona_injection(): formatted prompt block (capped). Empty if disabled.
"""
from __future__ import annotations

from .injection import build_persona_injection
from .loader import load, reload, switch
from .state import PersonaSnapshot, get_persona

__all__ = [
    "load",
    "reload",
    "switch",
    "get_persona",
    "build_persona_injection",
    "PersonaSnapshot",
]
