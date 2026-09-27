"""Curated skill hub index for install/browse UI."""
from __future__ import annotations

from typing import Iterable

from backend.skills.state import SkillManifest

CURATED_SKILL_HUB = [
    {
        "slug": "using-superpowers",
        "name": "Using Superpowers",
        "description": "Bootstraps Superpowers-style skill discovery and usage discipline.",
        "source_url": "https://github.com/obra/superpowers",
        "install_url": None,
        "permissions": {
            "execution": "disabled",
            "network": "not_required",
            "filesystem_write": "not_required",
        },
    },
    {
        "slug": "openai-docs",
        "name": "OpenAI Docs",
        "description": "Use current official OpenAI documentation when building with OpenAI products.",
        "source_url": "system",
        "install_url": None,
        "permissions": {
            "execution": "disabled",
            "network": "documentation_lookup",
            "filesystem_write": "not_required",
        },
    },
]


def build_hub_index(installed: Iterable[SkillManifest] = ()) -> list[dict]:
    installed_slugs = {skill.slug for skill in installed}
    out = []
    for entry in CURATED_SKILL_HUB:
        item = dict(entry)
        item["installed"] = item["slug"] in installed_slugs
        out.append(item)
    return out
