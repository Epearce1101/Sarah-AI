"""Mood/affinity endpoints for a conversation."""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException

from backend.api.schemas import MoodUpdate

router = APIRouter()

try:
    from backend.mood import (
        Emotion,
        get_mood_engine,
        get_or_create_mood_state,
        save_mood_state,
    )
    MOOD_SYSTEM_AVAILABLE = True
except ImportError as e:
    MOOD_SYSTEM_AVAILABLE = False
    logging.warning(f"[api.mood] Mood system not available: {e}")


@router.get("/api/conversations/{conversation_id}/mood")
def api_get_mood(conversation_id: int):
    """Get current mood state for a conversation."""
    if not MOOD_SYSTEM_AVAILABLE:
        return {
            "ok": False,
            "error": "Mood system not available",
            "mood": {
                "emotion": "neutral",
                "intensity": 0.2,
                "affinity": 0.9,
                "manual_override": False,
            },
        }

    try:
        mood = get_or_create_mood_state(conversation_id)
        engine = get_mood_engine()
        dials = engine.compute_dials(mood)
        avatar_params = engine.get_avatar_parameters(mood)

        return {
            "ok": True,
            "mood": {
                "emotion": mood.emotion.value,
                "intensity": mood.intensity,
                "affinity": mood.affinity,
                "manual_override": mood.manual_override,
                "updated_at": mood.updated_at,
            },
            "behavior_dials": {
                "warmth": dials.warmth,
                "formality": dials.formality,
                "verbosity": dials.verbosity,
                "initiative": dials.initiative,
                "directness": dials.directness,
                "caution": dials.caution,
                "playfulness": dials.playfulness,
                "empathy": dials.empathy,
            },
            "avatar_params": avatar_params,
        }
    except Exception as e:
        logging.exception(f"[GET /api/conversations/{conversation_id}/mood] failed")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/conversations/{conversation_id}/mood")
def api_update_mood(conversation_id: int, payload: MoodUpdate):
    """Update mood state for a conversation (manual override)."""
    if not MOOD_SYSTEM_AVAILABLE:
        return {"ok": False, "error": "Mood system not available"}

    try:
        mood = get_or_create_mood_state(conversation_id)

        if payload.emotion is not None:
            try:
                mood.emotion = Emotion(payload.emotion.lower())
            except ValueError:
                raise HTTPException(
                    status_code=400,
                    detail=f"Invalid emotion: {payload.emotion}. Valid options: {[e.value for e in Emotion]}",
                )

        if payload.intensity is not None:
            mood.intensity = max(0.0, min(1.0, payload.intensity))

        if payload.affinity is not None:
            mood.affinity = max(0.0, min(1.0, payload.affinity))

        if payload.manual_override is not None:
            mood.manual_override = payload.manual_override

        if payload.emotion is not None or payload.intensity is not None or payload.affinity is not None:
            mood.manual_override = True

        save_mood_state(mood)

        engine = get_mood_engine()
        dials = engine.compute_dials(mood)
        avatar_params = engine.get_avatar_parameters(mood)

        return {
            "ok": True,
            "mood": {
                "emotion": mood.emotion.value,
                "intensity": mood.intensity,
                "affinity": mood.affinity,
                "manual_override": mood.manual_override,
                "updated_at": mood.updated_at,
            },
            "behavior_dials": {
                "warmth": dials.warmth,
                "formality": dials.formality,
                "verbosity": dials.verbosity,
                "initiative": dials.initiative,
                "directness": dials.directness,
                "caution": dials.caution,
                "playfulness": dials.playfulness,
                "empathy": dials.empathy,
            },
            "avatar_params": avatar_params,
        }
    except HTTPException:
        raise
    except Exception as e:
        logging.exception(f"[POST /api/conversations/{conversation_id}/mood] failed")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/conversations/{conversation_id}/mood/reset")
def api_reset_mood(conversation_id: int):
    """Reset mood to neutral and clear manual override."""
    if not MOOD_SYSTEM_AVAILABLE:
        return {"ok": False, "error": "Mood system not available"}

    try:
        mood = get_or_create_mood_state(conversation_id)
        mood.emotion = Emotion.NEUTRAL
        mood.intensity = 0.2
        mood.affinity = 0.9
        mood.manual_override = False
        save_mood_state(mood)

        return {
            "ok": True,
            "mood": {
                "emotion": mood.emotion.value,
                "intensity": mood.intensity,
                "affinity": mood.affinity,
                "manual_override": mood.manual_override,
            },
        }
    except Exception as e:
        logging.exception(f"[POST /api/conversations/{conversation_id}/mood/reset] failed")
        raise HTTPException(status_code=500, detail=str(e))
