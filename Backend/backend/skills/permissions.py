"""Skill permission policy.

Current Sarah skills are prompt/context assets, not executable plugins. This
policy makes that explicit so future executable tools have to opt into a
separate permission surface instead of silently inheriting prompt-skill trust.
"""
from __future__ import annotations

from backend.config import settings
from backend.skills.state import SkillManifest


def describe_permission_policy() -> dict:
    return {
        "respect_permissions": settings.respect_permissions,
        "execution_default": "disabled",
        "network_default": "prompt",
        "filesystem_write_default": "prompt",
        "policy": (
            "Prompt skills may be enabled for context injection. Executable "
            "skills are not auto-run; they require a future explicit tool "
            "permission grant."
        ),
    }


def permission_summary_for_skill(skill: SkillManifest) -> dict:
    return {
        "slug": skill.slug,
        "name": skill.name,
        "enabled": skill.enabled,
        "can_execute_tools": False,
        "requires_execution_permission": True,
        "filesystem_write": "not_granted",
        "network": "not_granted",
    }
