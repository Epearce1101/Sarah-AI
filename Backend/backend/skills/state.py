"""In-memory skills snapshot, guarded by a lock.

Each `SkillManifest` is immutable. The list itself is replaced atomically
by the runtime loader, so readers always see a complete snapshot.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Tuple


@dataclass(frozen=True)
class SkillManifest:
    """Parsed snapshot of a single `SKILL.md` file."""

    slug: str
    name: str
    description: str
    body: str
    path: Optional[Path] = None
    enabled: bool = True
    stale: bool = False
    loaded_at: float = 0.0


_skills: Tuple[SkillManifest, ...] = ()
_lock = threading.Lock()


def set_skills(snapshot: List[SkillManifest]) -> None:
    """Atomically replace the in-memory snapshot."""
    global _skills
    with _lock:
        _skills = tuple(snapshot)


def get_skills() -> Tuple[SkillManifest, ...]:
    """Return the current snapshot tuple. Manifests are frozen — safe to share."""
    with _lock:
        return _skills
