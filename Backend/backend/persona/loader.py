"""Disk loader for Sarah's persona files.

Personas live under `settings.persona_workspace_path` (default: the repo
root, i.e. `Sarah_V10/personalities/`) so they no longer depend on an
OpenClaw workspace existing. Override with SARAH_PERSONA_WORKSPACE_PATH.

One-shot: called once at boot, plus on demand via `/api/persona/reload`.
No file watcher, no poll loop. Failures degrade gracefully — boot continues
with the empty default snapshot.

Resolution order:
    1. Read `personalities/_personality_state.json` → `active_personality`.
    2. Look for `personalities/<slug>/{IDENTITY,SOUL}.md`. Either present → use.
    3. If missing → empty snapshot, warning logged, boot continues.

Issue #23: the legacy top-level `~/.openclaw/workspace/{IDENTITY,SOUL}.md`
fallback was removed so stale Jessie/OpenClaw identity files can never bleed
into Sarah's runtime persona. Persona content now comes exclusively from the
active per-slug folder.
"""
from __future__ import annotations

import json
import logging
import re
import time
from pathlib import Path
from typing import Optional, Tuple

from backend.config.settings import settings

from .state import PersonaSnapshot, set_persona

logger = logging.getLogger(__name__)
_PERSONA_SLUG_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def load(workspace: Optional[Path] = None) -> PersonaSnapshot:
    """Read persona files from disk and install the snapshot."""
    root = workspace or settings.persona_workspace_path
    snapshot = _build_snapshot(root)
    set_persona(snapshot)

    summary = (
        f"slug={snapshot.slug or '(none)'} "
        f"identity_len={len(snapshot.identity_md)} "
        f"soul_len={len(snapshot.soul_md)} "
        f"fallback={snapshot.fallback_used}"
    )
    logger.info("[persona] loaded %s source=%s", summary, snapshot.source_dir)
    return snapshot


def reload() -> PersonaSnapshot:
    """Re-read persona files and replace the snapshot. Same as load()."""
    return load()


def switch(slug: str, workspace: Optional[Path] = None) -> PersonaSnapshot:
    """Select an OpenClaw persona slug, persist state, then reload."""
    slug = (slug or "").strip()
    if not _PERSONA_SLUG_RE.match(slug):
        raise ValueError("persona slug must be 1-64 letters, numbers, '_' or '-'")

    root = workspace or settings.persona_workspace_path
    persona_dir = root / "personalities" / slug
    if not persona_dir.exists() or not persona_dir.is_dir():
        raise FileNotFoundError(f"persona folder not found: {persona_dir}")

    if not ((persona_dir / "IDENTITY.md").exists() or (persona_dir / "SOUL.md").exists()):
        raise FileNotFoundError(f"persona {slug!r} has no IDENTITY.md or SOUL.md")

    state_file = root / "personalities" / "_personality_state.json"
    state_file.parent.mkdir(parents=True, exist_ok=True)
    state_file.write_text(
        json.dumps({"active_personality": slug}, indent=2) + "\n",
        encoding="utf-8",
    )
    return load(root)


def _read_active_slug(state_file: Path) -> Tuple[str, bool]:
    """Return (slug, ok). On error, returns ('', False) and logs."""
    if not settings.persona_use_active_state:
        return "", False
    try:
        text = state_file.read_text(encoding="utf-8")
    except FileNotFoundError:
        return "", False
    except OSError as exc:
        logger.warning("[persona] failed to read state file %s: %s", state_file, exc)
        return "", False

    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        logger.warning("[persona] state file %s is not valid JSON: %s", state_file, exc)
        return "", False

    slug = (data.get("active_personality") or "").strip()
    return slug, bool(slug)


def _safe_read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (FileNotFoundError, OSError):
        return ""


def _build_snapshot(workspace: Path) -> PersonaSnapshot:
    state_file = workspace / "personalities" / "_personality_state.json"
    slug, slug_ok = _read_active_slug(state_file)

    identity_md = ""
    soul_md = ""
    source_dir: Optional[Path] = None
    fallback_used = False

    if slug_ok:
        per_slug = workspace / "personalities" / slug
        candidate_identity = per_slug / "IDENTITY.md"
        candidate_soul = per_slug / "SOUL.md"
        identity_md = _safe_read(candidate_identity)
        soul_md = _safe_read(candidate_soul)
        if identity_md or soul_md:
            source_dir = per_slug

    # Issue #23: top-level OpenClaw IDENTITY.md/SOUL.md fallback disabled.
    # When no Sarah persona slug is set (or its folder is empty), return an
    # empty snapshot rather than loading the legacy Jessie/OpenClaw files.

    if not (identity_md or soul_md):
        logger.warning(
            "[persona] no persona files found under %s — using empty snapshot",
            workspace,
        )

    return PersonaSnapshot(
        slug=slug if slug_ok else "",
        identity_md=identity_md,
        soul_md=soul_md,
        source_dir=source_dir,
        state_file=state_file if slug_ok else None,
        fallback_used=fallback_used,
        loaded_at=time.time(),
    )
