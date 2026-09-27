"""Persona endpoints: read, switch, and reload OpenClaw persona snapshots.

Mirrors `backend/api/identity.py` (B3) — read-only surface for the frontend
to inspect the active persona and trigger a re-read after the user edits
the persona files. Persona-switching (writing `_personality_state.json`)
is deferred per `DESIGN_B5_plan.md §9`.
"""
from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, HTTPException

from backend.api.schemas import PersonaSwitchRequest
from backend.persona import (
    PersonaSnapshot,
    get_persona,
    reload as reload_persona,
    switch as switch_persona,
)

router = APIRouter()


def _serialize_meta(snapshot: PersonaSnapshot) -> Dict[str, Any]:
    return {
        "slug": snapshot.slug,
        "identity_len": len(snapshot.identity_md),
        "soul_len": len(snapshot.soul_md),
        "source_dir": str(snapshot.source_dir) if snapshot.source_dir else None,
        "state_file": str(snapshot.state_file) if snapshot.state_file else None,
        "fallback_used": snapshot.fallback_used,
        "loaded_at": snapshot.loaded_at,
    }


@router.get("/api/persona")
def api_persona() -> Dict[str, Any]:
    return _serialize_meta(get_persona())


@router.post("/api/persona/reload")
def api_persona_reload() -> Dict[str, Any]:
    snapshot = reload_persona()
    return _serialize_meta(snapshot)


@router.post("/api/persona/switch")
def api_persona_switch(payload: PersonaSwitchRequest) -> Dict[str, Any]:
    try:
        snapshot = switch_persona(payload.slug)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return _serialize_meta(snapshot)


@router.get("/api/persona/text")
def api_persona_text() -> Dict[str, Any]:
    snapshot = get_persona()
    return {
        **_serialize_meta(snapshot),
        "identity_md": snapshot.identity_md,
        "soul_md": snapshot.soul_md,
    }
