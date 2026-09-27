"""In-memory persona snapshot, guarded by a lock.

Mirrors the B3 identity-loader pattern: a frozen dataclass replaced
atomically by the loader so readers always see a complete snapshot.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass(frozen=True)
class PersonaSnapshot:
    """Parsed snapshot of the OpenClaw persona files for the active slug.

    Resolution order (handled by loader):
      1. `personalities/_personality_state.json` → active_personality
      2. `personalities/<slug>/{IDENTITY,SOUL}.md` if either present
      3. empty snapshot if all paths fail (Issue #23: no top-level fallback)
    """

    slug: str = ""
    identity_md: str = ""
    soul_md: str = ""
    source_dir: Optional[Path] = None
    state_file: Optional[Path] = None
    fallback_used: bool = False
    loaded_at: float = 0.0


_persona: PersonaSnapshot = PersonaSnapshot()
_lock = threading.Lock()


def set_persona(snapshot: PersonaSnapshot) -> None:
    """Atomically replace the in-memory snapshot. Called by the loader."""
    global _persona
    with _lock:
        _persona = snapshot


def get_persona() -> PersonaSnapshot:
    """Return the current snapshot (frozen — safe to share)."""
    with _lock:
        return _persona
