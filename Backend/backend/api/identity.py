"""Identity endpoints: read, write, and reload the OpenClaw USER.md snapshot.

Exposes the parsed USER.md content so the frontend can show who SARAH
thinks the user is, update USER.md, and trigger a re-read after external edits.
"""
from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter

from backend.api.schemas import IdentityUpdate
from backend.identity import (
    UserIdentity,
    get_user,
    reload as reload_identity,
    save_user_md,
)
from backend.openclaw_memory import load_openclaw_memory, serialize_openclaw_memory

router = APIRouter()


def _serialize(snapshot: UserIdentity) -> Dict[str, Any]:
    return {
        "name": snapshot.name,
        "call_name": snapshot.call_name,
        "pronouns": snapshot.pronouns,
        "timezone": snapshot.timezone,
        "notes": snapshot.notes,
        "source_path": str(snapshot.source_path) if snapshot.source_path else None,
        "loaded_at": snapshot.loaded_at,
    }


@router.get("/api/identity/user")
def api_identity_user() -> Dict[str, Any]:
    return _serialize(get_user())


@router.post("/api/identity/reload")
def api_identity_reload() -> Dict[str, Any]:
    snapshot = reload_identity()
    return _serialize(snapshot)


@router.put("/api/identity/user")
def api_identity_update(payload: IdentityUpdate) -> Dict[str, Any]:
    """Write USER.md in the OpenClaw workspace, then reload the snapshot."""
    snapshot = save_user_md(payload.model_dump())
    return _serialize(snapshot)


@router.get("/api/identity/memory")
def api_identity_memory() -> Dict[str, Any]:
    """Read OpenClaw MEMORY.md with the read-only conflict policy metadata."""
    return serialize_openclaw_memory(load_openclaw_memory())


@router.post("/api/identity/memory/reload")
def api_identity_memory_reload() -> Dict[str, Any]:
    """Stateless reload hook for frontend symmetry with USER.md/persona reloads."""
    return serialize_openclaw_memory(load_openclaw_memory())
