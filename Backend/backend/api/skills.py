"""Skill registration + enable/disable endpoints, plus disk discovery."""
from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException

from backend.api.schemas import SkillInstallUrlRequest, SkillRegister
from backend.models.core import get_all_skills, register_skill, set_skill_enabled
from backend.skills import (
    get_all_skills as get_skill_snapshot,
    reload as reload_skills,
)
from backend.skills.hub import build_hub_index
from backend.skills.installer import install_skill_from_url
from backend.skills.permissions import describe_permission_policy, permission_summary_for_skill

router = APIRouter()


def _serialise_manifest(m) -> dict:
    """Render a SkillManifest as a JSON-friendly dict."""
    return {
        "slug": m.slug,
        "name": m.name,
        "description": m.description,
        "path": str(m.path) if m.path else None,
        "enabled": m.enabled,
        "stale": m.stale,
        "body_len": len(m.body),
        "loaded_at": m.loaded_at,
    }


@router.get("/api/skills")
def api_get_skills():
    """Merged DB + disk view.

    DB rows are the authoritative source for `enabled`. The in-memory snapshot
    adds disk-derived metadata (`path`, `stale`, `body_len`) for any rows that
    were seen by the most recent discover().
    """
    db_rows = get_all_skills()
    snapshot_by_slug = {m.slug: m for m in get_skill_snapshot()}

    skills_out = []
    for row in db_rows:
        slug = row["slug"]
        manifest = snapshot_by_slug.get(slug)
        merged = dict(row)
        if manifest is not None:
            merged["stale"] = manifest.stale
            merged["body_len"] = len(manifest.body)
        else:
            # Row exists in DB but the loader hasn't seen it (e.g. it was
            # registered manually and never discovered on disk).
            merged["stale"] = False
            merged["body_len"] = 0
        skills_out.append(merged)

    return {"ok": True, "skills": skills_out}


@router.get("/api/skills/hub")
def api_get_skills_hub():
    """Return curated installable/browsable skills plus installed state."""
    return {
        "ok": True,
        "skills": build_hub_index(get_skill_snapshot()),
        "permission_policy": describe_permission_policy(),
    }


@router.get("/api/skills/permissions")
def api_get_skill_permissions():
    """Return executable permission policy for currently discovered skills."""
    snapshot = get_skill_snapshot()
    return {
        "ok": True,
        "policy": describe_permission_policy(),
        "skills": [permission_summary_for_skill(skill) for skill in snapshot],
    }


@router.post("/api/skills/register")
def api_register_skill(payload: SkillRegister):
    config_json = json.dumps(payload.config or {})
    register_skill(
        payload.name,
        payload.slug,
        payload.description or "",
        bool(payload.enabled if payload.enabled is not None else True),
        config_json,
    )
    return {"ok": True}


@router.post("/api/skills/install-url")
def api_install_skill_from_url(payload: SkillInstallUrlRequest):
    """Install a SKILL.md from a raw URL or GitHub tree/blob URL."""
    try:
        installed = install_skill_from_url(
            payload.url,
            overwrite=payload.overwrite,
        )
    except FileExistsError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    snapshot = reload_skills()
    if payload.enabled is not None:
        set_skill_enabled(str(installed["slug"]), bool(payload.enabled))
        snapshot = reload_skills()

    return {
        "ok": True,
        "installed": installed,
        "skills": [_serialise_manifest(m) for m in snapshot],
    }


@router.post("/api/skills/{slug}/enable")
def api_enable_skill(slug: str):
    set_skill_enabled(slug, True)
    return {"ok": True, "slug": slug, "enabled": True}


@router.post("/api/skills/{slug}/disable")
def api_disable_skill(slug: str):
    set_skill_enabled(slug, False)
    return {"ok": True, "slug": slug, "enabled": False}


@router.post("/api/skills/discover")
def api_discover_skills():
    """Re-scan `<openclaw_workspace>/skills/`, sync DB, return merged state."""
    snapshot = reload_skills()
    return {
        "ok": True,
        "count": len(snapshot),
        "fresh": sum(1 for m in snapshot if not m.stale),
        "stale": sum(1 for m in snapshot if m.stale),
        "skills": [_serialise_manifest(m) for m in snapshot],
    }


@router.get("/api/skills/{slug}/manifest")
def api_get_skill_manifest(slug: str):
    """Return the parsed manifest for a single skill, including its body."""
    for m in get_skill_snapshot():
        if m.slug == slug:
            payload = _serialise_manifest(m)
            payload["body"] = m.body
            return {"ok": True, "skill": payload}
    raise HTTPException(status_code=404, detail=f"skill {slug!r} not found")
