import base64
import io
import os
from dataclasses import dataclass
from typing import List, Optional, Tuple

import numpy as np

from faster_whisper import WhisperModel


@dataclass
class WakeWordResult:
    activated: bool
    transcript: str
    matched_phrase: Optional[str]


class WhisperWakeWordDetector:
    """
    High-accuracy wake-word detector using faster-whisper.

    Designed for short audio segments (0.5–5 seconds).
    You send a short clip, it transcribes it, and checks for wake phrases.
    """

    def __init__(
        self,
        model_size: str = None,
        device: str = None,
        compute_type: str = None,
        wake_phrases: Optional[List[str]] = None,
    ):
        # Model settings (env-configurable)
        self.model_size = model_size or os.getenv("WHISPER_MODEL", "small.en")
        self.device = device or os.getenv("WHISPER_DEVICE", "cuda" if self._has_cuda() else "cpu")
        self.compute_type = compute_type or os.getenv("WHISPER_COMPUTE_TYPE", "float16" if self.device == "cuda" else "int8")

        # Wake phrases (normalized to lowercase)
        default_phrases = [
            "hey sarah",
            "hey ai",
            "hey sarah-chan",
            "sarah can you hear me",
        ]
        if wake_phrases:
            default_phrases.extend(wake_phrases)
        self.wake_phrases = list({p.lower() for p in default_phrases})

        print(f"[WhisperWake] Loading model={self.model_size} device={self.device} type={self.compute_type}")
        self.model = WhisperModel(self.model_size, device=self.device, compute_type=self.compute_type)
        print("[WhisperWake] Model loaded.")

    # ------------------------------------------------------------------
    # Device helper
    # ------------------------------------------------------------------
    def _has_cuda(self) -> bool:
        try:
            import torch
            return torch.cuda.is_available()
        except Exception:
            return False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def detect_from_base64(self, audio_b64: str, mime_type: str = "audio/wav") -> WakeWordResult:
        """
        Decode base64 audio, run whisper, detect wake-word.
        The frontend should send short clips (1–3 seconds).
        """
        if not audio_b64:
            return WakeWordResult(False, "", None)

        raw_bytes = base64.b64decode(audio_b64)
        return self.detect_from_bytes(raw_bytes, mime_type=mime_type)

    def detect_from_bytes(self, audio_bytes: bytes, mime_type: str = "audio/wav") -> WakeWordResult:
        """
        audio_bytes: raw contents of a WAV/OGG/MP3/WEBM file.
        mime_type is just for debugging here.
        """
        # Load into float32 waveform using soundfile or ffmpeg
        samples, sr = self._decode_audio(audio_bytes)
        if samples is None or len(samples) == 0:
            return WakeWordResult(False, "", None)

        transcript = self._transcribe(samples, sr)
        matched = self._match_wake_phrase(transcript)

        activated = matched is not None
        return WakeWordResult(activated=activated, transcript=transcript, matched_phrase=matched)

    # ------------------------------------------------------------------
    # Audio decode helpers
    # ------------------------------------------------------------------
    def _decode_audio(self, audio_bytes: bytes) -> Tuple[Optional[np.ndarray], int]:
        """
        Decode audio bytes to mono float32 numpy array + sample rate.

        Uses soundfile if available, otherwise falls back to ffmpeg via ffmpeg-python.
        """
        try:
            import soundfile as sf
        except ImportError:
            sf = None

        if sf is not None:
            try:
                data, sr = sf.read(io.BytesIO(audio_bytes), dtype="float32", always_2d=True)
                if data.ndim == 2:
                    data = np.mean(data, axis=1)
                return data, sr
            except Exception as e:
                print("[WhisperWake] soundfile decode failed:", e)

        # Fallback: use ffmpeg-python if installed
        try:
            import ffmpeg

            # Write to in-memory buffer via pipe
            process = (
                ffmpeg
                .input("pipe:0")
                .output(
                    "pipe:1",
                    format="s16le",
                    acodec="pcm_s16le",
                    ac=1,
                    ar="16000"
                )
                .run_async(pipe_stdin=True, pipe_stdout=True, pipe_stderr=True)
            )
            out, err = process.communicate(input=audio_bytes)
            if process.returncode != 0:
                print("[WhisperWake] ffmpeg error:", err.decode(errors="ignore"))
                return None, 0

            audio = np.frombuffer(out, np.int16).astype("float32") / 32768.0
            return audio, 16000
        except Exception as e:
            print("[WhisperWake] ffmpeg decode failed:", e)
            return None, 0

    # ------------------------------------------------------------------
    # Transcription
    # ------------------------------------------------------------------
    def _transcribe(self, samples: np.ndarray, sr: int) -> str:
        """
        Run faster-whisper on the given audio.
        For short clips this is fast even with small model.
        """
        # faster-whisper expects 16k, resample if needed
        if sr != 16000:
            try:
                import resampy
                samples = resampy.resample(samples, sr, 16000)
                sr = 16000
            except Exception:
                pass

        segments, _ = self.model.transcribe(samples, beam_size=1, vad_filter=True)
        text_parts = []
        for seg in segments:
            text_parts.append(seg.text.strip())
        transcript = " ".join(text_parts).strip()
        print(f"[WhisperWake] Transcript: {transcript}")
        return transcript.lower()

    # ------------------------------------------------------------------
    # Matching
    # ------------------------------------------------------------------
    def _match_wake_phrase(self, transcript: str) -> Optional[str]:
        if not transcript:
            return None

        for phrase in self.wake_phrases:
            if phrase in transcript:
                print(f"[WhisperWake] Matched wake phrase: {phrase}")
                return phrase
        return None
