"""Vosk-based wake-word listener.

Pulls audio from the default WASAPI input device at 48 kHz, downsamples to
16 kHz int16, and feeds it to a Vosk recognizer. Detected wake phrases are
pushed to the module-level ``wake_events`` queue, which ``GET /api/wake``
drains.

``start_wake_listener()`` is idempotent and meant to be called from the
FastAPI lifespan startup hook.
"""
from __future__ import annotations

import json
import queue
import threading
import time
from typing import Optional

import numpy as np
from vosk import Model, KaldiRecognizer
import sounddevice as sd

from backend.config import settings as _settings
from backend.audio.wake_diagnostics import (
    configure_wake_diagnostics,
    record_wake_transcript,
    set_wake_listener_error,
)
from backend.audio.wake_words import (
    WakeMatcherState,
    extract_vosk_confidence,
    get_wake_grammar,
    get_wake_prefixes,
    get_wake_words,
    match_wake_transcript,
)

WAKE_MODEL_PATH = str(_settings.wake_model_path)
WAKE_WORDS = get_wake_words()
WAKE_PREFIXES = get_wake_prefixes()
WAKE_GRAMMAR = get_wake_grammar()

wake_events: "queue.Queue[str]" = queue.Queue()
_thread: Optional[threading.Thread] = None


def _wake_word_listener() -> None:
    try:
        print("[WAKE] Initializing Vosk wake model...")
        model = Model(WAKE_MODEL_PATH)
        recognizer = KaldiRecognizer(model, 16000, json.dumps(WAKE_GRAMMAR))
        try:
            recognizer.SetWords(True)
        except Exception:
            pass

        matcher_state = WakeMatcherState()
        configure_wake_diagnostics(listener_running=True)
        last_raw_log_at = 0.0

        def maybe_log_raw(source: str, text: str, confidence: float | None, important: bool = False) -> None:
            nonlocal last_raw_log_at
            if not text:
                return
            # Issue #30: only emit raw-transcript chatter when explicitly enabled.
            if not _settings.wake_raw_transcript_logging:
                return
            now = time.monotonic()
            interval = max(0.1, _settings.wake_raw_log_interval_seconds)
            if not important and now - last_raw_log_at < interval:
                return
            last_raw_log_at = now
            conf = "unknown" if confidence is None else f"{confidence:.2f}"
            print(f"[WAKE RAW] {source} text='{text}' confidence={conf}")

        def match_wake(text: str, source: str, confidence: float | None = None) -> bool:
            now = time.monotonic()
            result = match_wake_transcript(
                text,
                matcher_state,
                now=now,
                wake_words=WAKE_WORDS,
                prefixes=WAKE_PREFIXES,
                prefix_enabled=_settings.wake_prefix_fallback_enabled,
                allow_prefix_fallback=source == "final",
                prefix_hits_required=_settings.wake_prefix_fallback_hits,
                prefix_window_seconds=_settings.wake_prefix_fallback_window_seconds,
                cooldown_seconds=_settings.wake_cooldown_seconds,
                confidence=confidence,
                min_confidence=_settings.wake_min_confidence,
                single_prefix_confidence=_settings.wake_prefix_single_confidence,
            )
            # Issue #30: per-utterance debug lines (prefix-pending / low-confidence /
            # cooldown) gated behind verbose flag so they don't flood the terminal.
            if _settings.wake_raw_transcript_logging:
                if result.reason == "prefix_pending":
                    needed = max(1, _settings.wake_prefix_fallback_hits)
                    print(f"[WAKE DEBUG] Prefix fallback hit: {result.match} ({result.prefix_hit_count}/{needed})")
                elif result.reason == "low_confidence":
                    conf = "n/a" if result.confidence is None else f"{result.confidence:.2f}"
                    print(f"[WAKE DEBUG] Suppressed low-confidence {result.match_type}: {result.match} (confidence={conf})")
                elif result.reason == "cooldown":
                    print(f"[WAKE DEBUG] Suppressed wake during cooldown: {result.match}")

            if result.wake:
                suffix = " prefix fallback" if result.match_type == "prefix_fallback" else ""
                print(f"[WAKE] Wake word detected ({source}{suffix}): {result.match}")
                wake_events.put("wake")
            record_wake_transcript(
                text=text,
                source=source,
                result=result,
                matcher_last_wake_at=matcher_state.last_wake_at,
                event_pending=not wake_events.empty(),
                now_monotonic=now,
            )
            return result.wake

        audio_q: "queue.Queue[bytes]" = queue.Queue()

        def audio_callback(indata, frames, time_info, status):
            if status:
                print(f"[WAKE] Audio status: {status}")

            data = indata[:, 0].copy()
            downsample_ratio = max(1, round(_settings.wake_input_samplerate / 16000))
            downsampled = data[::downsample_ratio]
            pcm16 = (
                (downsampled * 32767.0)
                .clip(-32768, 32767)
                .astype(np.int16)
                .tobytes()
            )
            audio_q.put(pcm16)

        device = _settings.wake_input_device or None
        stream = sd.InputStream(
            samplerate=_settings.wake_input_samplerate,
            channels=1,
            dtype="float32",
            callback=audio_callback,
            device=device,
        )

        stream.start()
        # Issue #30: collapse verbose boot dump into a single concise line.
        # Detailed config (phrases, prefix-fallback knobs, cooldown, grammar size)
        # is now only emitted when SARAH_WAKE_RAW_TRANSCRIPT_LOGGING=true.
        print(f"[WAKE] Engine started: {len(WAKE_WORDS)} phrase(s), grammar={len(WAKE_GRAMMAR)}, device={device or 'default'}.")
        if _settings.wake_raw_transcript_logging:
            print(f"[WAKE] Phrases: {', '.join(WAKE_WORDS)}")
            print(
                f"[WAKE] Prefix fallback: enabled={_settings.wake_prefix_fallback_enabled}, "
                f"phrases={', '.join(WAKE_PREFIXES)}, hits={_settings.wake_prefix_fallback_hits}, "
                f"final_only=True"
            )
            print(
                f"[WAKE] Cooldown={_settings.wake_cooldown_seconds}s, "
                f"min_confidence={_settings.wake_min_confidence}, "
                f"single_prefix_confidence={_settings.wake_prefix_single_confidence}"
            )
            print(f"[WAKE] Samplerate={_settings.wake_input_samplerate}, downsampled to 16000Hz mono.")

        while True:
            data = audio_q.get()

            if recognizer.AcceptWaveform(data):
                result = json.loads(recognizer.Result())
                text = (result.get("text") or "").lower().strip()
                confidence = extract_vosk_confidence(result)
                if text:
                    maybe_log_raw("final", text, confidence)
                    match_wake(text, "final", confidence)
            else:
                partial = json.loads(recognizer.PartialResult()).get("partial", "")
                partial = partial.lower().strip()
                if partial:
                    maybe_log_raw("partial", partial, None)
                    if match_wake(partial, "partial"):
                        recognizer = KaldiRecognizer(model, 16000, json.dumps(WAKE_GRAMMAR))
                        try:
                            recognizer.SetWords(True)
                        except Exception:
                            pass

    except Exception as e:
        print("[WAKE] ERROR in wake-word thread:", e)
        set_wake_listener_error(str(e))


def start_wake_listener() -> None:
    """Start the wake-word thread once. Safe to call repeatedly."""
    global _thread
    if _thread is not None and _thread.is_alive():
        return
    _thread = threading.Thread(target=_wake_word_listener, daemon=True)
    _thread.start()


def is_wake_listener_running() -> bool:
    """Return whether the wake-word thread is alive."""
    return _thread is not None and _thread.is_alive()
