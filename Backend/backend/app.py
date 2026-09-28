"""FastAPI app factory.

``create_app()`` builds the SARAH AI FastAPI application: middleware,
extracted routers, and the startup/shutdown lifecycle hooks. The
``backend.server`` module is a thin shim that exports ``app = create_app()``
so launch tooling and uvicorn can keep targeting ``backend.server:app``.
"""
from __future__ import annotations

import asyncio
import hmac
from contextlib import asynccontextmanager
import logging

import requests
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from backend import llm_models
from backend.api import register_routers
from backend.audio.wake_loop import start_wake_listener
from backend.config import settings
from backend.db import init_db
from backend.identity import load as load_identity
from backend.state import preload_sarah

logger = logging.getLogger(__name__)

API_TOKEN_HEADER = "X-Sarah-Token"
# The launcher polls health before Electron (the token carrier) exists.
_AUTH_EXEMPT_PATHS = frozenset({"/api/health"})


class _EndpointFilter(logging.Filter):
    """Hide noisy polling endpoints from uvicorn access logs."""

    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        return "GET /api/wake" not in msg and "GET /health/vision" not in msg


def create_app() -> FastAPI:
    """Build and return the FastAPI application."""
    logging.getLogger("uvicorn.access").addFilter(_EndpointFilter())

    app = FastAPI(
        title="SARAH AI Backend",
        description="Backend for SARAH AI (chat, TTS, screen capture, vision).",
        version="0.1.0",
        lifespan=_lifespan,
    )

    # Binding to 127.0.0.1 doesn't stop a web page in the user's browser from
    # calling us (CORS is wide open for the file:// renderer), and several
    # routes write files, run code, or push git. When a token is configured,
    # require it on every request; Electron's main process injects it.
    # Registered before CORS so CORS wraps it: preflights are answered before
    # reaching this check, and 401s still carry CORS headers (otherwise the
    # renderer only sees an opaque "Failed to fetch").
    expected_token = settings.api_token
    if expected_token:
        @app.middleware("http")
        async def _require_api_token(request: Request, call_next):
            if request.method == "OPTIONS" or request.url.path in _AUTH_EXEMPT_PATHS:
                return await call_next(request)
            supplied = request.headers.get(API_TOKEN_HEADER, "")
            if not hmac.compare_digest(supplied, expected_token):
                return JSONResponse(
                    status_code=401,
                    content={"ok": False, "detail": "missing or invalid API token"},
                )
            return await call_next(request)

        logger.info("[INIT] API token enforcement enabled.")

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    register_routers(app)
    return app


@asynccontextmanager
async def _lifespan(app: FastAPI):
    await _startup()
    from backend.agency.mind import mind_loop
    from backend.agency.reminders import reminder_loop

    background = [
        asyncio.create_task(_start_ollama_manager()),
        asyncio.create_task(_backup_loop()),
        asyncio.create_task(reminder_loop()),
        asyncio.create_task(mind_loop()),
    ]
    try:
        yield
    finally:
        for task in background:
            if not task.done():
                task.cancel()
        await _shutdown()


BACKUP_CHECK_INTERVAL_SECONDS = 6 * 3600


async def _backup_loop() -> None:
    """Daily DB snapshot: checked at boot, then every 6 h (runs off-loop)."""
    from backend.backup import ensure_recent_backup

    while True:
        await asyncio.to_thread(ensure_recent_backup)
        await asyncio.sleep(BACKUP_CHECK_INTERVAL_SECONDS)


async def _startup() -> None:
    logger.info("[INIT] Booting SQL database...")
    init_db()
    logger.info("[INIT] SQL ready.")

    # Restore a model picked in the UI, then check (off the event loop) that
    # it and its fallbacks still exist on OpenRouter.
    llm_models.load_persisted_model()
    asyncio.get_running_loop().run_in_executor(None, llm_models.check_availability)

    logger.info("[INIT] Loading identity from USER.md...")
    try:
        load_identity()
        logger.info("[INIT] Identity ready.")
    except Exception as e:
        logger.warning(f"[INIT] Identity load failed (using fallback): {e}")

    logger.info("[INIT] Loading persona from IDENTITY.md / SOUL.md...")
    try:
        from backend.persona import load as load_persona
        psnap = load_persona()
        slug = psnap.slug or "(none)"
        logger.info(f"[INIT] Persona ready: slug={slug} "
            f"identity={len(psnap.identity_md)}c soul={len(psnap.soul_md)}c "
            f"fallback={psnap.fallback_used}")
    except Exception as e:
        logger.warning(f"[INIT] Persona load failed (continuing without persona): {e}")

    logger.info("[INIT] Discovering skills...")
    try:
        from backend.skills import load as load_skills
        snap = load_skills()
        disk_count = sum(1 for s in snap if not s.stale)
        stale_count = sum(1 for s in snap if s.stale)
        logger.info(f"[INIT] Skills ready: {disk_count} on disk, {stale_count} stale.")
    except Exception as e:
        logger.warning(f"[INIT] Skills load failed (continuing without skills): {e}")

    logger.info("[INIT] Initializing memory system...")
    try:
        from backend.memory import get_memory_store
        get_memory_store()
        logger.info("[INIT] Memory system ready.")
    except Exception as e:
        logger.warning(f"[INIT] Memory system unavailable: {e}")

    preload_sarah()
    if settings.wake_listener_enabled:
        start_wake_listener()
    else:
        logger.info("[INIT] Wake-word listener off (live voice listens instead; SARAH_WAKE_LISTENER=1 to enable).")

    # Load + warm speech recognition off the startup path so the first live
    # voice turn doesn't pay model load and CUDA setup (~5 s).
    def _prewarm_stt():
        try:
            from backend.whisper_stt import get_whisper_stt
            stt = get_whisper_stt()
            logger.info(f"[INIT] Speech recognition ready ({stt.model_size} on {stt.device}).")
        except Exception as exc:
            logger.warning(f"[INIT] Speech recognition unavailable: {exc}")

    import threading
    threading.Thread(target=_prewarm_stt, name="stt-prewarm", daemon=True).start()

    logger.info("[INIT] Prewarming Piper TTS daemon...")
    try:
        from backend.piper.piper_tts import prewarm as prewarm_piper
        prewarm_piper()
        logger.info("[INIT] Piper daemon spawned (voice loads in background).")
    except Exception as e:
        logger.warning(f"[INIT] Piper prewarm failed (will lazy-spawn on first call): {e}")

    logger.info("[INIT] Starting Ollama manager...")


async def _shutdown() -> None:
    logger.info(f"\n{'=' * 60}")
    logger.info("[SHUTDOWN] Backend shutdown initiated")
    logger.info("=" * 60)

    logger.info("[SHUTDOWN] Stopping Ollama manager...")
    try:
        from backend.services.ollama_manager import get_ollama_manager
        ollama_mgr = get_ollama_manager()
        await ollama_mgr.stop_monitoring()
        ollama_mgr.cleanup()
    except Exception as e:
        logger.error(f"[SHUTDOWN] Ollama manager cleanup error (best-effort): {e}")

    logger.info("[SHUTDOWN] Cleaning up vision manager...")
    try:
        from backend.services.vision import cleanup_vision_manager
        await cleanup_vision_manager()
    except Exception as e:
        logger.error(f"[SHUTDOWN] Vision cleanup error (best-effort): {e}")

    logger.info("[SHUTDOWN] Tearing down Piper TTS daemon...")
    try:
        from backend.piper.piper_tts import cleanup_warm_proc
        cleanup_warm_proc()
    except Exception as e:
        logger.error(f"[SHUTDOWN] Piper cleanup error (best-effort): {e}")

    logger.info(f"\n{'=' * 60}")
    logger.info("[SHUTDOWN] All services stopped - Backend offline")
    logger.info(f"{'=' * 60}\n")


async def _start_ollama_manager() -> None:
    if not settings.ollama_exe_path.exists():
        # Cloud-only setup: no local models, so nothing to start or monitor
        # (it used to retry and log an ERROR every few seconds).
        logger.info("[INIT] Ollama not installed; local model mode unavailable (cloud models in use).")
        return
    try:
        from backend.services.ollama_manager import get_ollama_manager
        ollama_mgr = get_ollama_manager()

        logger.info("[INIT] Starting Ollama server and performing health check...")
        is_healthy = await ollama_mgr.start_and_verify_simple()

        status = ollama_mgr.get_status()
        logger.info(f"\n{'=' * 60}")
        logger.info("[OLLAMA STATUS REPORT]")
        logger.info(f"  Status: {status['status'].upper()}")
        logger.info(f"  Monitoring: "
            f"{'Active' if status['monitoring_active'] else 'Inactive'}")
        logger.info(f"  Health Check Interval: {status['health_check_interval']}s "
            f"({status['health_check_interval'] // 60} minutes)")
        if is_healthy:
            logger.info("\n  [OK] OLLAMA IS OPERATIONAL AND READY")
            await asyncio.to_thread(_prewarm_ollama_text_model)
        else:
            logger.info(f"\n  [!] OLLAMA STATUS: {status['status']}")
        logger.info(f"{'=' * 60}\n")

        await ollama_mgr.start_monitoring()
    except Exception as e:
        error_msg = str(e).encode("ascii", "replace").decode("ascii")
        logger.error(f"[INIT] Ollama manager error: {error_msg}")


def _prewarm_ollama_text_model() -> None:
    """Load the local chat model before the first user message."""
    url = f"{settings.ollama_base_url.rstrip('/')}/api/chat"
    payload = {
        "model": settings.default_local_model,
        "messages": [{"role": "user", "content": "ready"}],
        "stream": False,
        "keep_alive": "30m",
        "options": {
            "num_predict": 1,
            "num_ctx": settings.local_context_window_tokens,
            "temperature": 0.1,
            "stop": list(settings.local_stop_sequences),
        },
    }
    try:
        logger.info(f"[INIT] Prewarming Ollama text model: {settings.default_local_model}")
        response = requests.post(
            url,
            json=payload,
            timeout=settings.local_request_timeout_seconds,
        )
        response.raise_for_status()
        logger.info("[INIT] Ollama text model prewarmed.")
    except Exception as exc:
        logger.warning(f"[INIT] Ollama text prewarm failed: {exc}")
