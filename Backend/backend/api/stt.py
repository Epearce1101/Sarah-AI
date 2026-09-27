"""Speech-to-text endpoints (Whisper)."""
from __future__ import annotations

import base64
import logging
import traceback
import time
from pathlib import Path

from fastapi import APIRouter, HTTPException

from backend.api.schemas import STTRequest, STTRequest2
from backend.whisper_stt import get_whisper_stt
from backend.diagnostics.telemetry import record_voice_latency

logger = logging.getLogger(__name__)

router = APIRouter()

CURRENT_DIR = Path(__file__).resolve().parent.parent  # backend/


@router.post("/api/stt_legacy")
def api_stt_legacy(payload: STTRequest):
    """Legacy STT path: WebM base64 → Whisper transcription via temp file."""
    try:
        stt = get_whisper_stt()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Whisper load failed: {e}")

    try:
        audio_bytes = base64.b64decode(payload.audio_b64)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid base64: {e}")

    temp_path = CURRENT_DIR / "temp_stt.webm"
    try:
        temp_path.write_bytes(audio_bytes)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"File write failed: {e}")

    try:
        text = stt(str(temp_path))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"STT failed: {e}")
    finally:
        try:
            temp_path.unlink(missing_ok=True)
        except Exception:
            pass

    return {"ok": True, "text": text.strip() if text else ""}


@router.post("/api/stt")
def api_stt(payload: STTRequest2):
    started_at = time.perf_counter()
    logger.debug(f"[STT] Received request, audio size: {len(payload.audio_b64)}, mime: {payload.mime_type}")

    try:
        logger.debug("[STT] Getting Whisper STT instance...")
        stt = get_whisper_stt()
        logger.debug("[STT] Whisper instance ready, starting transcription...")

        result = stt.stt_from_base64(payload.audio_b64, payload.mime_type)
        logger.debug(f"[STT] Transcription complete: {result}")

        if not result.get("ok"):
            logger.error(f"[STT] Transcription failed: {result.get('error')}")
            raise HTTPException(status_code=500, detail=result.get("error", "stt_failed"))

        logger.debug(f"[STT] Success! Text: {result.get('text', '')}")
        record_voice_latency("stt", (time.perf_counter() - started_at) * 1000, ok=True)
        return {"ok": True, "text": result.get("text", "")}
    except Exception as e:
        record_voice_latency("stt", (time.perf_counter() - started_at) * 1000, ok=False)
        logger.error(f"[STT] Exception occurred: {e}")
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))
