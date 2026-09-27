"""Skills package — disk-discovered SKILL.md manifests, prompt-injection only.

Public surface:
    - load() / reload(): scan `<openclaw_workspace>/skills/`, parse manifests,
      sync the DB, replace the in-memory snapshot.
    - get_all_skills(): all manifests including stale ones.
    - get_enabled_skills(): only enabled, non-stale manifests for injection.
    - build_skill_injection(): render the system-prompt block (with char cap).
    - parse_skill_md(): pure parser, used by tests.
    - SkillManifest: the frozen snapshot dataclass.
"""
from __future__ import annotations

from .loader import parse_skill_md
from .runtime import load, reload, get_all_skills, get_enabled_skills
from .state import SkillManifest
from .injection import build_skill_injection

__all__ = [
    "load",
    "reload",
    "get_all_skills",
    "get_enabled_skills",
    "build_skill_injection",
    "parse_skill_md",
    "SkillManifest",
]
