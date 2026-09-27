"""Skills runtime — disk discovery, DB sync, snapshot maintenance."""
from __future__ import annotations

import logging
import shutil
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from backend.config.settings import settings
from backend.models.core import (
    get_all_skills as db_get_all_skills,
    register_skill_from_disk,
)

from .loader import parse_manifest_file
from .state import SkillManifest, get_skills, set_skills

logger = logging.getLogger(__name__)


def _skills_root(workspace: Optional[Path] = None) -> Path:
    if workspace is not None:
        return workspace / "skills"
    return settings.skills_path


def _seed_from_openclaw_if_empty(target: Path) -> int:
    """First-run import: copy skills from `~/.openclaw/workspace/skills/` into
    the project-local skills dir if the project-local dir is missing or empty.

    Returns the number of skill folders copied. Idempotent — once the project
    dir has any skills, this is a no-op.
    """
    if not settings.skills_seed_from_openclaw:
        return 0

    target.mkdir(parents=True, exist_ok=True)
    has_existing = any(p.is_dir() for p in target.iterdir())
    if has_existing:
        return 0

    source = settings.openclaw_workspace_path / "skills"
    if not source.exists() or not source.is_dir():
        return 0

    copied = 0
    for entry in sorted(source.iterdir(), key=lambda p: p.name):
        if not entry.is_dir():
            continue
        if not (entry / "SKILL.md").exists():
            continue
        dest = target / entry.name
        if dest.exists():
            continue
        try:
            shutil.copytree(entry, dest)
            copied += 1
        except Exception as exc:  # noqa: BLE001 — defensive; never abort boot
            logger.warning("[skills] seed copy failed for %r: %s", entry.name, exc)

    if copied:
        logger.info(
            "[skills] seeded %d skill(s) from %s -> %s",
            copied, source, target,
        )
    return copied


def _scan_disk(root: Path) -> List[Dict[str, object]]:
    if not root.exists() or not root.is_dir():
        return []
    found: List[Dict[str, object]] = []
    for entry in sorted(root.iterdir(), key=lambda p: p.name):
        if not entry.is_dir():
            continue
        manifest_path = entry / "SKILL.md"
        if not manifest_path.exists():
            continue
        parsed = parse_manifest_file(manifest_path)
        if parsed is None:
            continue
        found.append(parsed)
    return found


def _resolve_collisions(parsed: List[Dict[str, object]]) -> List[Dict[str, object]]:
    """First-discovered wins; later entries with the same slug get a warning."""
    seen: Dict[str, Dict[str, object]] = {}
    for item in parsed:
        slug = item["slug"]  # type: ignore[index]
        if slug in seen:
            logger.warning(
                "[skills] slug collision for %r at %s — keeping %s",
                slug, item["path"], seen[slug]["path"],
            )
            continue
        seen[slug] = item
    return list(seen.values())


def _build_snapshot(
    parsed: List[Dict[str, object]],
    db_rows: List[dict],
    now: float,
) -> Tuple[SkillManifest, ...]:
    parsed_by_slug = {item["slug"]: item for item in parsed}  # type: ignore[index]
    db_by_slug = {row["slug"]: row for row in db_rows}

    snapshots: List[SkillManifest] = []

    for slug, item in parsed_by_slug.items():
        db_row = db_by_slug.get(slug)
        # If the row exists in DB, that flag wins (preserves user choice
        # across reloads). New rows take the manifest's enabled_default.
        if db_row is not None:
            enabled = bool(db_row.get("enabled", 1))
        else:
            enabled = bool(item.get("enabled_default", True))
        snapshots.append(SkillManifest(
            slug=slug,
            name=item["name"],  # type: ignore[arg-type]
            description=item["description"],  # type: ignore[arg-type]
            body=item["body"],  # type: ignore[arg-type]
            path=item["path"],  # type: ignore[arg-type]
            enabled=enabled,
            stale=False,
            loaded_at=now,
        ))

    # Stale rows: in DB but no matching disk manifest. Surface them so the
    # user can purge via API. Body is empty — they cannot inject.
    for slug, db_row in db_by_slug.items():
        if slug in parsed_by_slug:
            continue
        path_value = db_row.get("path")
        snapshots.append(SkillManifest(
            slug=slug,
            name=db_row.get("name", slug),
            description=db_row.get("description", "") or "",
            body="",
            path=Path(path_value) if path_value else None,
            enabled=bool(db_row.get("enabled", 1)),
            stale=True,
            loaded_at=now,
        ))

    snapshots.sort(key=lambda s: s.slug)
    return tuple(snapshots)


def discover(workspace: Optional[Path] = None) -> Tuple[SkillManifest, ...]:
    """Re-scan disk, sync DB, replace the in-memory snapshot. Returns it."""
    root = _skills_root(workspace)
    if workspace is None:
        _seed_from_openclaw_if_empty(root)
    parsed = _resolve_collisions(_scan_disk(root))

    for item in parsed:
        try:
            register_skill_from_disk(
                slug=item["slug"],  # type: ignore[arg-type]
                name=item["name"],  # type: ignore[arg-type]
                description=item["description"],  # type: ignore[arg-type]
                path=str(item["path"]),
                enabled_default=bool(item.get("enabled_default", True)),
            )
        except Exception as exc:  # noqa: BLE001 — defensive; never abort boot
            logger.warning(
                "[skills] DB upsert failed for %r: %s", item.get("slug"), exc,
            )

    db_rows = db_get_all_skills()
    snapshot = _build_snapshot(parsed, db_rows, time.time())
    set_skills(list(snapshot))

    logger.info(
        "[skills] discovered %d on-disk, %d total in DB (%d stale)",
        len(parsed), len(db_rows),
        sum(1 for s in snapshot if s.stale),
    )
    return snapshot


def load() -> Tuple[SkillManifest, ...]:
    """Boot-time entry point. Wraps discover() and degrades gracefully."""
    try:
        return discover()
    except Exception as exc:  # noqa: BLE001 — never abort boot
        logger.warning("[skills] load failed: %s — using empty snapshot", exc)
        set_skills([])
        return ()


def reload() -> Tuple[SkillManifest, ...]:
    """Same as load() but always raises if discovery itself errors —
    callers (the reload API) want the error to surface."""
    return discover()


def get_all_skills() -> Tuple[SkillManifest, ...]:
    return get_skills()


def get_enabled_skills() -> Tuple[SkillManifest, ...]:
    return tuple(s for s in get_skills() if s.enabled and not s.stale and s.body)
