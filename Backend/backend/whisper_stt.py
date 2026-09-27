import base64
import io
import os
from typing import Optional, Tuple, Dict, Any

import numpy as np
from faster_whisper import WhisperModel

from backend.config import settings as _settings
from backend.timing import stage as _timing_stage


class WhisperSTT:
    """Whisper-based STT for short voice commands.

    Wake-word detection lives in `backend.audio.wake_loop` (Vosk daemon thread)
    and is not handled here.
    """

    def __init__(
        self,
        model_size: Optional[str] = None,
        device: Optional[str] = None,
        compute_type: Optional[str] = None,
    ):
        # Model size
        self.model_size = model_size or os.getenv("WHISPER_MODEL", "tiny.en")

        # Force CPU always — prevents float16 GPU crash
        self.device = "cpu"

        # Force int8 for CPU — fastest & safest
        self.compute_type = "int8"

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

    def stt_from_base64(self, audio_b64: str, mime_type: str = "audio/webm") -> Dict[str, Any]:
        with _timing_stage("stt.decode", mime=mime_type):
            samples, sr = self._decode_b64(audio_b64, mime_type)
        if samples is None:
            return {"ok": False, "text": "", "error": "decode_failed"}

        audio_seconds = len(samples) / sr if sr else 0.0
        print(f"[WhisperSTT] Audio decoded: {len(samples)} samples at {sr} Hz ({audio_seconds:.2f} seconds)")

        with _timing_stage("stt.transcribe", audio_seconds=f"{audio_seconds:.2f}"):
            transcript = self._transcribe(samples, sr)
        print(f"[WhisperSTT] Transcription result: '{transcript}'")
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

        # Fall back to pydub (more flexible ffmpeg handling)
        try:
            from pydub import AudioSegment
            # Set ffmpeg path explicitly for Windows
            AudioSegment.converter = str(_settings.ffmpeg_path)
            AudioSegment.ffprobe = str(_settings.ffprobe_path)
            audio_segment = AudioSegment.from_file(io.BytesIO(audio_bytes))
            # Convert to mono 16kHz
            audio_segment = audio_segment.set_channels(1).set_frame_rate(16000)
            # Get raw samples
            samples = np.array(audio_segment.get_array_of_samples()).astype("float32") / 32768.0
            print(f"[WhisperSTT] pydub decode successful, {len(samples)} samples")
            return samples, 16000
        except Exception as e:
            print("[WhisperSTT] pydub decode failed:", e)

        # Fall back to ffmpeg-python direct
        try:
            import ffmpeg
            import subprocess
            # Use explicit ffmpeg path
            ffmpeg_path = str(_settings.ffmpeg_path)
            cmd = [
                ffmpeg_path, "-i", "pipe:0",
                "-f", "s16le", "-acodec", "pcm_s16le",
                "-ac", "1", "-ar", "16000",
                "pipe:1"
            ]
            process = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE
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

        print(f"[WhisperSTT] Starting transcription with Whisper...")
        segments, info = self.model.transcribe(samples, beam_size=1, vad_filter=False, language="en")

        # Don't print info object directly - it may contain unicode characters
        print(f"[WhisperSTT] Transcription completed")

        text_parts = [seg.text.strip() for seg in segments]
        print(f"[WhisperSTT] Segments found: {len(text_parts)}")
        for i, seg_text in enumerate(text_parts):
            try:
                print(f"[WhisperSTT] Segment {i}: '{seg_text}'")
            except UnicodeEncodeError:
                print(f"[WhisperSTT] Segment {i}: <contains unicode>")

        transcript = " ".join(text_parts).strip().lower()

        try:
            print(f"[WhisperSTT] Final transcript: '{transcript}'")
        except UnicodeEncodeError:
            print(f"[WhisperSTT] Final transcript length: {len(transcript)} chars")

        return transcript


# ============================================================
# SINGLETON EXPORTED FOR FASTAPI
# ============================================================

whisper_stt: Optional[WhisperSTT] = None


def get_whisper_stt() -> WhisperSTT:
    global whisper_stt
    if whisper_stt is None:
        whisper_stt = WhisperSTT()
    return whisper_stt
