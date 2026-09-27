"""Shared mutable state for the SARAH backend.

Holds the SarahCore singleton and the LLM mode globals that routers and
lifespan handlers read. Kept deliberately small so circular imports stay easy
to reason about.
"""
from __future__ import annotations

import logging
from typing import Optional

from backend.config import settings as _settings

logger = logging.getLogger(__name__)

SarahCore = None
try:
    from backend.sarah_core import SarahCore  # type: ignore
except Exception as e:
    logger.error("%s %s", "[ERROR] Failed to import SarahCore:", e)

_sarah: Optional["SarahCore"] = None

# Runtime LLM mode globals. Routers MUST mutate these via ``set_llm_mode`` so
# every consumer sees the same value. Reads should use attribute access on
# this module (``backend.state.LLM_MODE``), not ``from ... import LLM_MODE``.
LLM_MODE: str = _settings.llm_mode.lower()
if LLM_MODE == "online" and not _settings.openrouter_api_key:
    logging.warning("[LLM MODE] OpenRouter key missing; starting in local Ollama mode.")
    LLM_MODE = "local"
LOCAL_LLM_MODEL: str = _settings.default_local_model

# Eagerly probe the optional screen-capture stack so health endpoints and
# vision routes can render a single, consistent answer without re-importing.
SCREEN_ENABLED: bool = False
SCREEN_IMPORT_ERROR: Optional[str] = None
try:
    from backend.screen.screen_capture import start_recording  # noqa: F401
    SCREEN_ENABLED = True
except Exception as _screen_err:
    SCREEN_IMPORT_ERROR = str(_screen_err)


def set_llm_mode(mode: str, local_model: Optional[str] = None) -> None:
    """Update the in-process LLM mode globals."""
    global LLM_MODE, LOCAL_LLM_MODEL
    LLM_MODE = mode.lower()
    if local_model is not None:
        LOCAL_LLM_MODEL = local_model


def get_sarah() -> "SarahCore":
    """Lazily build the SarahCore singleton, surfacing real tracebacks."""
    global _sarah

    if _sarah is not None:
        return _sarah

    try:
        from backend.sarah_core import SarahCore as _SarahCore
    except Exception as e:
        import traceback
        logger.error("\n=========== SARAHCORE IMPORT ERROR ===========")
        traceback.print_exc()
        logger.info("==============================================\n")
        raise RuntimeError(f"SarahCore crashed during import: {e}")

    if _SarahCore is None:
        raise RuntimeError("SarahCore imported as None; cannot handle chat.")

    logger.info("[SARAH INIT] Initializing SarahCore...")

    try:
        _sarah = _SarahCore()
    except Exception as e:
        import traceback
        logger.error("\n=========== SARAHCORE INSTANTIATION ERROR ===========")
        traceback.print_exc()
        logger.info("=====================================================\n")
        raise RuntimeError(f"SarahCore failed to initialize: {e}")

    logger.info("%s %s", "[SARAH INIT] SarahCore initialized. Core version:", getattr(_sarah, "core_version", "unknown"))

    try:
        if hasattr(_sarah, "set_llm_mode"):
            _sarah.set_llm_mode(LLM_MODE, LOCAL_LLM_MODEL)
    except Exception as e:
        logging.warning(f"[LLM MODE] Could not set initial mode: {e}")

    return _sarah


def preload_sarah() -> None:
    """Best-effort warmup; failures don't abort startup."""
    try:
        get_sarah()
        logger.info("[BACKEND] SarahCore preloaded.")
    except Exception as e:
        logger.warning("%s %s", "[BACKEND] Could not preload SarahCore:", e)
