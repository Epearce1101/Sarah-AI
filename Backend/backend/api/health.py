"""Health endpoints: /api/health, /health/vision, /api/vision/health."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

from fastapi import APIRouter, HTTPException

from backend import state
from backend.state import get_sarah
import logging

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/api/health")
def api_health():
    info: Dict[str, Any] = {
        "ok": True,
        "screen_enabled": state.SCREEN_ENABLED,
        "screen_error": state.SCREEN_IMPORT_ERROR,
    }

    try:
        sarah = get_sarah()
        info["sarah_version"] = getattr(sarah, "core_version", "unknown")
        info["llm_mode"] = state.LLM_MODE
    except Exception as e:
        info["ok"] = False
        info["error"] = str(e)

    return info


@router.post("/api/shutdown")
def api_shutdown():
    """Graceful stop for the launcher (runs the lifespan cleanup).

    Only enabled when an API token is configured: then the token middleware
    has already authenticated the caller. Without a token, any local page
    could stop the backend, so it's refused.
    """
    from backend.config import settings
    from backend import lifecycle

    if not settings.api_token:
        raise HTTPException(status_code=403, detail="shutdown requires API token mode")
    if not lifecycle.request_shutdown():
        raise HTTPException(status_code=503, detail="server runner not managing shutdown")
    return {"ok": True, "shutting_down": True}


@router.get("/api/state")
def api_state():
    """Core snapshot for `window.sarahAPI.fetchState()` in preload.js."""
    try:
        return {"ok": True, **get_sarah().get_state_snapshot()}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@router.get("/health/vision")
@router.get("/api/vision/health")  # Alias for frontend compatibility
async def health_vision():
    """Real-time vision service health status."""
    from backend.services.vision import get_vision_manager
    from backend.services.ollama_manager import get_ollama_manager

    vision = get_vision_manager()
    ollama_mgr = get_ollama_manager()

    health = await vision.health_check()
    ollama_status = ollama_mgr.get_status()
    health["ollama_manager"] = ollama_status

    logger.debug(f"[HealthVision] vision_ready={health.get('vision_ready')}, "
        f"warmup_done={health.get('warmup_done')}, "
        f"model_available={health.get('ok')}, "
        f"models={health.get('models_available', [])[:3]}")

    return health


@router.get("/api/diagnostics/ffmpeg")
def diagnostics_ffmpeg():
    """Expose the shared FFmpeg path resolver for setup diagnostics."""
    from backend.screen.ffmpeg_paths import resolve_ffmpeg_diagnostics

    screen_dir = Path(__file__).resolve().parent.parent / "screen"
    return resolve_ffmpeg_diagnostics(screen_dir)
