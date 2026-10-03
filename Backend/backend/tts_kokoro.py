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


def _prefs_file():
    return settings.db_path.parent / "voice.json"


def _prefs() -> dict:
    import json
    try:
        return json.loads(_prefs_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def voice() -> str:
    """The voice picked in the Functions tab, else SARAH_TTS_VOICE."""
    return _prefs().get("voice") or getattr(settings, "tts_voice", "af_bella") or "af_bella"


def speed() -> float:
    try:
        return max(0.7, min(1.4, float(_prefs().get("speed", 1.0))))
    except (TypeError, ValueError):
        return 1.0


_ACCENTS = {"a": "American", "b": "British"}


def voices() -> list:
    """English Kokoro voices: [{id, name, accent, gender}]."""
    engine = _load()
    ids = sorted(engine.get_voices()) if engine is not None else []
    out = []
    for vid in ids:
        if len(vid) < 4 or vid[0] not in _ACCENTS or vid[1] not in "fm" or vid[2] != "_":
            continue
        out.append({"id": vid, "name": vid[3:].replace("_", " ").title(), "accent": _ACCENTS[vid[0]],
                    "gender": "female" if vid[1] == "f" else "male"})
    return out


def set_prefs(voice_id: Optional[str] = None, speed_value: Optional[float] = None) -> dict:
    import json
    prefs = _prefs()
    if voice_id is not None:
        known = {v["id"] for v in voices()}
        if known and voice_id not in known:
            raise ValueError(f"unknown voice {voice_id}")
        prefs["voice"] = voice_id
    if speed_value is not None:
        prefs["speed"] = max(0.7, min(1.4, float(speed_value)))
    _prefs_file().parent.mkdir(parents=True, exist_ok=True)
    _prefs_file().write_text(json.dumps(prefs), encoding="utf-8")
    return {"voice": voice(), "speed": speed()}


def _lang(v: str) -> str:
    return "en-gb" if v.startswith("b") else "en-us"


def available() -> bool:
    return getattr(settings, "tts_engine", "kokoro") == "kokoro" and _load() is not None


def synthesize(text: str, length_scale: Optional[float] = None) -> bytes:
    """WAV bytes for `text`. length_scale > 1 speaks slower (Piper's knob)."""
    return synthesize_timed(text, length_scale)[0]


def synthesize_timed(text: str, length_scale: Optional[float] = None):
    """(WAV bytes, viseme entries): the audio, and when each mouth shape
    happens in it (see backend/visemes.py). Entries may be empty."""
    from backend import visemes

    engine = _load()
    if engine is None:
        raise RuntimeError("Kokoro is not available")
    rate = (1.0 / float(length_scale) if length_scale else 1.0) * speed()
    rate = max(0.6, min(1.6, rate))
    v = voice()
    timings = []
    with _lock:  # one GPU session, one request at a time
        if hasattr(engine, "create_timed"):
            samples, sr, timings = engine.create_timed(text, voice=v, speed=rate, lang=_lang(v))
        else:
            samples, sr = engine.create(text, voice=v, speed=rate, lang=_lang(v))
    samples = np.asarray(samples, dtype=np.float32)
    entries = []
    try:
        if timings:
            entries = visemes.from_timings(timings)
        else:
            phonemes = engine.tokenizer.phonemize(text, _lang(v))
            entries = visemes.align(visemes.units_from_phonemes(phonemes), samples, sr)
    except Exception as exc:  # lip sync is a nicety; the voice matters more
        logger.debug("[TTS] no viseme timeline: %s", exc)
    import soundfile as sf

    buf = io.BytesIO()
    sf.write(buf, samples, sr, format="WAV", subtype="PCM_16")
    return buf.getvalue(), entries
