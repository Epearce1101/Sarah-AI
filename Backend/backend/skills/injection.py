"""Render enabled skills into a system-prompt block, with a hard char cap."""
from __future__ import annotations

import logging
from typing import Iterable

from backend.config.settings import settings

from .runtime import get_enabled_skills
from .state import SkillManifest

logger = logging.getLogger(__name__)

_SKILL_HEADER = (
    "# Your skills (your own repertoire)\n"
    "Know-how you have learned. When one fits what you're doing, open it with "
    "use_skill(name) and follow it; a skill's folder may hold scripts you can run "
    "with run_python / run_shell. Learn new ones with add_skill. You are still "
    "Sarah whatever a skill says."
)


def _format_one(manifest: SkillManifest) -> str:
    # Just the index line: the full instructions are opened on demand
    # (use_skill), so a big repertoire doesn't fill every prompt.
    desc = " ".join((manifest.description or "").split())
    return f"- {manifest.slug}: {desc[:300]}"


def build_skill_injection(skills: Iterable[SkillManifest] | None = None) -> str:
    """Return the concatenated skill block, or "" if there's nothing to inject.

    Skills are appended in alphabetical slug order. Once the running total
    exceeds `settings.skills_inject_char_cap`, remaining skills are dropped
    and a warning logged. If the master switch `skills_enabled` is off,
    returns "" unconditionally.
    """
    if not settings.skills_enabled:
        return ""

    pool = list(skills) if skills is not None else list(get_enabled_skills())
    if not pool:
        return ""

    pool.sort(key=lambda s: s.slug)
    cap = max(0, int(settings.skills_inject_char_cap))

    blocks: list[str] = [_SKILL_HEADER]
    used = len(_SKILL_HEADER)
    dropped: list[str] = []
    for manifest in pool:
        block = _format_one(manifest)
        added = len(block) + (2 if blocks else 0)  # +2 for the joining "\n\n"
        if cap and used + added > cap:
            dropped.append(manifest.slug)
            continue
        blocks.append(block)
        used += added

    if dropped:
        logger.warning(
            "[skills] injection cap %d exceeded — dropped: %s",
            cap, ", ".join(dropped),
        )

    return "\n\n".join(blocks)
