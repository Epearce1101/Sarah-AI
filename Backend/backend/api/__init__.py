"""HTTP API routers.

Each module in this package owns one domain (health, conversations, mood, ...)
and exposes either a FastAPI ``APIRouter`` named ``router`` or a function
``register(app)``. ``register_routers(app)`` wires them all to the app.

During the B1 P3 Step 4 split the routers are extracted incrementally; this
function only includes routers that have been moved out of ``server.py``.
"""
from __future__ import annotations

from fastapi import FastAPI


def register_routers(app: FastAPI) -> None:
    """Attach all extracted routers to the given FastAPI app."""
    from backend.api.health import router as health_router
    from backend.api.settings import router as settings_router
    from backend.api.context import router as context_router
    from backend.api.logs import router as logs_router
    from backend.api.skills import router as skills_router
    from backend.api.memories import router as memories_router
    from backend.api.mood import router as mood_router
    from backend.api.conversations import router as conversations_router
    from backend.api.timezone import router as time_router
    from backend.api.projects import router as projects_router
    from backend.api.projects_git import router as projects_git_router
    from backend.api.wake import router as wake_router
    from backend.api.stt import router as stt_router
    from backend.api.tts import router as tts_router
    from backend.api.chat import router as chat_router
    from backend.api.vision import router as vision_router
    from backend.api.ollama import router as ollama_router
    from backend.api.code_tools import router as code_tools_router
    from backend.api.identity import router as identity_router
    from backend.api.persona import router as persona_router
    from backend.api.diagnostics import router as diagnostics_router

    for r in (
        health_router,
        settings_router,
        context_router,
        logs_router,
        skills_router,
        memories_router,
        mood_router,
        conversations_router,
        time_router,
        projects_router,
        projects_git_router,
        wake_router,
        stt_router,
        tts_router,
        chat_router,
        vision_router,
        ollama_router,
        code_tools_router,
        identity_router,
        persona_router,
        diagnostics_router,
    ):
        app.include_router(r)
