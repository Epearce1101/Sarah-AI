"""Text-to-speech endpoint (Piper)."""
from __future__ import annotations

import logging
import time
from pathlib import Path

from fastapi import APIRouter, HTTPException, Response

from backend.api.schemas import TTSRequest
from backend.diagnostics.telemetry import record_voice_latency

logger = logging.getLogger(__name__)

try:
    from backend.piper.piper_tts import piper_tts
except Exception as e:
    logger.error("%s %s", "[ERROR] Piper TTS import failed:", e)
    piper_tts = None

router = APIRouter()


@router.get("/api/tts/voices")
def tts_voices():
    """Voices she can speak with, and the current choice (Functions tab)."""
    from backend import tts_kokoro

    return {"available": tts_kokoro.available(), "voices": tts_kokoro.voices(),
            "voice": tts_kokoro.voice(), "speed": tts_kokoro.speed()}


@router.post("/api/tts/voice")
def tts_set_voice(payload: dict):
    """Pick her voice / speaking speed; applies to the next sentence."""
    from backend import tts_kokoro

    try:
        return {"ok": True, **tts_kokoro.set_prefs(payload.get("voice"), payload.get("speed"))}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/api/tts")
def api_tts(req: TTSRequest):
    started_at = time.perf_counter()
    # Her voice: Kokoro (natural), Piper only as a fallback.
    from backend import tts_kokoro

    text = (req.text or "").strip()
    if text and tts_kokoro.available():
        try:
            wav_bytes = tts_kokoro.synthesize(text, req.length_scale)
            record_voice_latency("tts", (time.perf_counter() - started_at) * 1000, ok=True)
            return Response(content=wav_bytes, media_type="audio/wav",
                            headers={"Content-Disposition": 'inline; filename="sarah_tts.wav"'})
        except Exception:
            logger.exception("[/api/tts] Kokoro failed; falling back to Piper")

    if piper_tts is None:
        record_voice_latency("tts", 0, ok=False)
        raise HTTPException(status_code=500, detail="Piper TTS is not available.")

    text = req.text.strip()
    if not text:
        record_voice_latency("tts", (time.perf_counter() - started_at) * 1000, ok=False)
        raise HTTPException(status_code=400, detail="TTS text is empty.")

    try:
        wav_path = piper_tts(
            text=text,
            length_scale=req.length_scale,
            noise_scale=req.noise_scale,
            noise_w=req.noise_w,
        )

        wav_path = Path(wav_path).resolve()
        if not wav_path.exists():
            raise FileNotFoundError(f"Piper output file not found: {wav_path}")

        wav_bytes = wav_path.read_bytes()
    except Exception as e:
        logging.exception("[/api/tts] Piper TTS failed")
        record_voice_latency("tts", (time.perf_counter() - started_at) * 1000, ok=False)
        raise HTTPException(status_code=500, detail=str(e))

    record_voice_latency("tts", (time.perf_counter() - started_at) * 1000, ok=True)

    return Response(
        content=wav_bytes,
        media_type="audio/wav",
        headers={"Content-Disposition": 'inline; filename="sarah_tts.wav"'},
    )
