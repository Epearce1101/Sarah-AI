"""Sarah's voice: Kokoro (natural neural TTS), on the GPU when available.

Model files live in Backend/models/kokoro (kokoro-v1.0.onnx, voices-v1.0.bin).
Voice and engine are settings (SARAH_TTS_VOICE, default af_bella). Piper is
the fallback if Kokoro can't load.
"""
from __future__ import annotations

import io
import logging
import threading
from typing import Optional

import numpy as np

from backend.config import settings

logger = logging.getLogger("sarah.tts")

_lock = threading.Lock()
_engine = None
_failed = False


def _load():
    global _engine, _failed
    if _engine is not None or _failed:
        return _engine
    with _lock:
        if _engine is not None or _failed:
            return _engine
        try:
            from backend.whisper_stt import _add_cuda_dll_dirs  # NVIDIA DLLs from the venv

            _add_cuda_dll_dirs()
            import onnxruntime as ort
            from kokoro_onnx import Kokoro

            ort.set_default_logger_severity(3)
            folder = settings.models_dir / "kokoro"
            sess = ort.InferenceSession(str(folder / "kokoro-v1.0.onnx"),
                                        providers=["CUDAExecutionProvider", "CPUExecutionProvider"])
            _engine = Kokoro.from_session(sess, str(folder / "voices-v1.0.bin"))
            device = sess.get_providers()[0].replace("ExecutionProvider", "")
            _engine.create("Hi.", voice=voice(), speed=1.0, lang=_lang(voice()))  # warm up
            logger.info("[TTS] Kokoro ready (%s, voice %s)", device, voice())
        except Exception as exc:
            _failed = True
            logger.warning("[TTS] Kokoro unavailable, using Piper: %s", exc)
        return _engine


def voice() -> str:
    return getattr(settings, "tts_voice", "af_bella") or "af_bella"


def _lang(v: str) -> str:
    return "en-gb" if v.startswith("b") else "en-us"


def available() -> bool:
    return getattr(settings, "tts_engine", "kokoro") == "kokoro" and _load() is not None


def synthesize(text: str, length_scale: Optional[float] = None) -> bytes:
    """WAV bytes for `text`. length_scale > 1 speaks slower (Piper's knob)."""
    engine = _load()
    if engine is None:
        raise RuntimeError("Kokoro is not available")
    speed = 1.0 / float(length_scale) if length_scale else 1.0
    speed = max(0.7, min(1.4, speed))
    with _lock:  # one GPU session, one request at a time
        samples, sr = engine.create(text, voice=voice(), speed=speed, lang=_lang(voice()))
    import soundfile as sf

    buf = io.BytesIO()
    sf.write(buf, np.asarray(samples, dtype=np.float32), sr, format="WAV", subtype="PCM_16")
    return buf.getvalue()
