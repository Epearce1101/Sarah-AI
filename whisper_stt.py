import base64
import io
import os
from typing import Optional, Tuple, Dict, Any

import numpy as np
from faster_whisper import WhisperModel


class WhisperSTT:
    """
    Whisper-based STT for:
    - wake-word detection
    - short voice commands
    """

    def __init__(
        self,
        model_size: Optional[str] = None,
        device: Optional[str] = None,
        compute_type: Optional[str] = None,
        wake_phrases=None,
    ):
        # ---------------------------
        # FORCE SAFE, FAST SETTINGS
        # ---------------------------

        # Model size
        self.model_size = model_size or os.getenv("WHISPER_MODEL", "small.en")

        # Force CPU always — prevents float16 GPU crash
        self.device = "cpu"

        # Force int8 for CPU — fastest & safest
        self.compute_type = "int8"

        # ---------------------------
        # WAKE WORDS
        # ---------------------------
        default_wake = [
            "hey sarah",
            "hey ai",
            "hey sarah-chan",
            "sarah can you hear me",
            "alright gorgeous",
            "alright babe",
            "hey love",
        ]
        if wake_phrases:
            default_wake.extend(wake_phrases)

        # Normalize & dedupe
        self.wake_phrases = list({p.lower() for p in default_wake})

        # ---------------------------
        # LOAD WHISPER MODEL
        # ---------------------------

        print(
            f"[WhisperSTT] Loading model={self.model_size} "
            f"device={self.device} type={self.compute_type}"
        )

        self.model = WhisperModel(
            self.model_size,
            device=self.device,
            compute_type=self.compute_type,
        )

        print("[WhisperSTT] Model loaded.")

    # ======================================================
    # PUBLIC API
    # ======================================================

    def wake_from_base64(self, audio_b64: str, mime_type: str = "audio/webm") -> Dict[str, Any]:
        samples, sr = self._decode_b64(audio_b64, mime_type)
        if samples is None:
            return {"ok": False, "activated": False, "transcript": "", "matched_phrase": None}

        transcript = self._transcribe(samples, sr)
        matched = self._match_wake_phrase(transcript)
        return {
            "ok": True,
            "activated": matched is not None,
            "transcript": transcript,
            "matched_phrase": matched,
        }

    def stt_from_base64(self, audio_b64: str, mime_type: str = "audio/webm") -> Dict[str, Any]:
        samples, sr = self._decode_b64(audio_b64, mime_type)
        if samples is None:
            return {"ok": False, "text": "", "error": "decode_failed"}

        transcript = self._transcribe(samples, sr)
        return {"ok": True, "text": transcript}

    # ======================================================
    # AUDIO DECODING HELPERS
    # ======================================================

    def _decode_b64(self, audio_b64: str, mime_type: str) -> Tuple[Optional[np.ndarray], int]:
        try:
            audio_bytes = base64.b64decode(audio_b64)
        except Exception as e:
            print("[WhisperSTT] base64 decode failed:", e)
            return None, 0
        return self._decode_bytes(audio_bytes)

    def _decode_bytes(self, audio_bytes: bytes) -> Tuple[Optional[np.ndarray], int]:
        # First try using soundfile
        try:
            import soundfile as sf
            data, sr = sf.read(io.BytesIO(audio_bytes), dtype="float32", always_2d=True)
            if data.ndim == 2:
                data = np.mean(data, axis=1)
            return data, sr
        except Exception as e:
            print("[WhisperSTT] soundfile failed:", e)

        # Fall back to ffmpeg decode
        try:
            import ffmpeg
            process = (
                ffmpeg
                .input("pipe:0")
                .output(
                    "pipe:1",
                    format="s16le",
                    acodec="pcm_s16le",
                    ac=1,
                    ar="16000",
                )
                .run_async(pipe_stdin=True, pipe_stdout=True, pipe_stderr=True)
            )
            out, err = process.communicate(input=audio_bytes)
            if process.returncode != 0:
                print("[WhisperSTT] ffmpeg error:", err.decode(errors="ignore"))
                return None, 0

            audio = np.frombuffer(out, np.int16).astype("float32") / 32768.0
            return audio, 16000

        except Exception as e:
            print("[WhisperSTT] ffmpeg decode failed:", e)
            return None, 0

    # ======================================================
    # WHISPER TRANSCRIPTION
    # ======================================================

    def _transcribe(self, samples: np.ndarray, sr: int) -> str:
        # Resample to 16k if needed
        if sr != 16000:
            try:
                import resampy
                samples = resampy.resample(samples, sr, 16000)
                sr = 16000
            except Exception as e:
                print("[WhisperSTT] resample failed:", e)

        segments, _ = self.model.transcribe(samples, beam_size=1, vad_filter=True)
        text_parts = [seg.text.strip() for seg in segments]
        transcript = " ".join(text_parts).strip().lower()

        print(f"[WhisperSTT] Transcript: {transcript}")
        return transcript

    # ======================================================
    # WAKE WORD MATCHING
    # ======================================================

    def _match_wake_phrase(self, transcript: str) -> Optional[str]:
        if not transcript:
            return None
        for phrase in self.wake_phrases:
            if phrase in transcript:
                print(f"[WhisperSTT] Matched wake phrase: {phrase}")
                return phrase
        return None


# ============================================================
# SINGLETON EXPORTED FOR FASTAPI
# ============================================================

whisper_stt: Optional[WhisperSTT] = None


def get_whisper_stt() -> WhisperSTT:
    global whisper_stt
    if whisper_stt is None:
        whisper_stt = WhisperSTT()
    return whisper_stt
