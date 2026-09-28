import base64
import io
import os
from typing import Optional, Tuple, Dict, Any

import numpy as np
from faster_whisper import WhisperModel

from backend.config import settings as _settings
from backend.timing import stage as _timing_stage
import logging

logger = logging.getLogger(__name__)


def _add_cuda_dll_dirs() -> bool:
    """Make the NVIDIA runtime wheels (nvidia-cublas-cu12, nvidia-cudnn-cu12)
    in the venv visible to CTranslate2. Returns True if any were found."""
    import glob
    import site

    found = False
    roots = [p for p in site.getsitepackages() if os.path.isdir(os.path.join(p, "nvidia"))]
    for root in roots:
        for d in glob.glob(os.path.join(root, "nvidia", "*", "bin")):
            try:
                os.add_dll_directory(d)
            except (OSError, AttributeError):
                continue
            os.environ["PATH"] = d + os.pathsep + os.environ.get("PATH", "")
            found = True
    return found


def _cuda_available() -> bool:
    try:
        import ctranslate2
        return ctranslate2.get_cuda_device_count() > 0
    except Exception:
        return False


class WhisperSTT:
    """Whisper speech recognition (faster-whisper).

    Runs on the GPU (large-v3-turbo, ~0.25 s per 5 s of speech on an RTX
    3060) when CUDA is available, else on the CPU with a small model. Models
    download into ``settings.models_dir`` (the project folder), never C:.
    Wake-word detection lives in ``backend.audio.wake_loop``; continuous live
    voice in ``backend.voice``.
    """

    def __init__(
        self,
        model_size: Optional[str] = None,
        device: Optional[str] = None,
        compute_type: Optional[str] = None,
    ):
        import threading

        self._lock = threading.Lock()
        wanted = (device or _settings.whisper_device or "auto").lower()
        use_gpu = wanted in ("auto", "cuda") and _add_cuda_dll_dirs() and _cuda_available()
        download_root = str(_settings.models_dir / "hf" / "whisper")

        self.model = None
        if use_gpu:
            # SARAH_WHISPER_MODEL; the legacy WHISPER_MODEL env var was a CPU-era
            # choice (e.g. small.en) and only applies to the CPU fallback.
            self.model_size = model_size or _settings.whisper_model
            self.device, self.compute_type = "cuda", compute_type or "int8_float16"
            try:
                logger.info(f"[WhisperSTT] Loading model={self.model_size} device=cuda type={self.compute_type}")
                self.model = WhisperModel(self.model_size, device="cuda", compute_type=self.compute_type,
                                          download_root=download_root)
            except Exception as exc:
                logger.warning(f"[WhisperSTT] GPU load failed ({exc}); falling back to CPU")
                self.model = None
        if self.model is None:
            self.model_size = os.getenv("WHISPER_MODEL") or _settings.whisper_cpu_model
            self.device, self.compute_type = "cpu", "int8"
            logger.info(f"[WhisperSTT] Loading model={self.model_size} device=cpu type=int8")
            self.model = WhisperModel(self.model_size, device="cpu", compute_type="int8",
                                      download_root=download_root)

        self._warmup()
        logger.info(f"[WhisperSTT] Ready: {self.model_size} on {self.device}")

    def _warmup(self) -> None:
        """The first CUDA transcription pays ~3 s of kernel setup; pay it now."""
        try:
            silence = np.zeros(16000, dtype=np.float32)
            with self._lock:
                list(self.model.transcribe(silence, language="en", beam_size=1, without_timestamps=True)[0])
        except Exception as exc:
            logger.debug(f"[WhisperSTT] warmup skipped: {exc}")

    def transcribe_array(self, samples: np.ndarray, *, prompt: Optional[str] = None) -> str:
        """16 kHz mono float32 -> text, original casing (live voice path)."""
        if samples is None or len(samples) < 1600:
            return ""
        with self._lock:
            segments, _info = self.model.transcribe(
                samples.astype(np.float32, copy=False),
                language="en",
                beam_size=1,
                vad_filter=False,
                without_timestamps=True,
                condition_on_previous_text=False,
                initial_prompt=prompt,
            )
            text = " ".join(seg.text.strip() for seg in segments)
        return text.strip()

    # ======================================================
    # PUBLIC API
    # ======================================================

    def stt_from_base64(self, audio_b64: str, mime_type: str = "audio/webm") -> Dict[str, Any]:
        with _timing_stage("stt.decode", mime=mime_type):
            samples, sr = self._decode_b64(audio_b64, mime_type)
        if samples is None:
            return {"ok": False, "text": "", "error": "decode_failed"}

        audio_seconds = len(samples) / sr if sr else 0.0
        logger.info(f"[WhisperSTT] Audio decoded: {len(samples)} samples at {sr} Hz ({audio_seconds:.2f} seconds)")

        with _timing_stage("stt.transcribe", audio_seconds=f"{audio_seconds:.2f}"):
            transcript = self._transcribe(samples, sr)
        logger.info(f"[WhisperSTT] Transcription result: '{transcript}'")
        return {"ok": True, "text": transcript}

    # ======================================================
    # AUDIO DECODING HELPERS
    # ======================================================

    def _decode_b64(self, audio_b64: str, mime_type: str) -> Tuple[Optional[np.ndarray], int]:
        try:
            audio_bytes = base64.b64decode(audio_b64)
        except Exception as e:
            logger.warning("%s %s", "[WhisperSTT] base64 decode failed:", e)
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
            logger.warning("%s %s", "[WhisperSTT] soundfile failed:", e)

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
            logger.info(f"[WhisperSTT] pydub decode successful, {len(samples)} samples")
            return samples, 16000
        except Exception as e:
            logger.warning("%s %s", "[WhisperSTT] pydub decode failed:", e)

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
                logger.error("%s %s", "[WhisperSTT] ffmpeg error:", err.decode(errors="ignore"))
                return None, 0

            audio = np.frombuffer(out, np.int16).astype("float32") / 32768.0
            return audio, 16000

        except Exception as e:
            logger.warning("%s %s", "[WhisperSTT] ffmpeg decode failed:", e)
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
                logger.warning("%s %s", "[WhisperSTT] resample failed:", e)

        logger.info(f"[WhisperSTT] Starting transcription with Whisper...")
        with self._lock:
            segments, info = self.model.transcribe(samples, beam_size=1, vad_filter=False, language="en")
            segments = list(segments)

        # Don't print info object directly - it may contain unicode characters
        logger.info(f"[WhisperSTT] Transcription completed")

        text_parts = [seg.text.strip() for seg in segments]
        logger.info(f"[WhisperSTT] Segments found: {len(text_parts)}")
        for i, seg_text in enumerate(text_parts):
            try:
                logger.info(f"[WhisperSTT] Segment {i}: '{seg_text}'")
            except UnicodeEncodeError:
                logger.info(f"[WhisperSTT] Segment {i}: <contains unicode>")

        transcript = " ".join(text_parts).strip().lower()

        try:
            logger.info(f"[WhisperSTT] Final transcript: '{transcript}'")
        except UnicodeEncodeError:
            logger.info(f"[WhisperSTT] Final transcript length: {len(transcript)} chars")

        return transcript


# ============================================================
# SINGLETON EXPORTED FOR FASTAPI
# ============================================================

whisper_stt: Optional[WhisperSTT] = None
_singleton_lock = __import__("threading").Lock()


def get_whisper_stt() -> WhisperSTT:
    global whisper_stt
    with _singleton_lock:  # a warmup thread and a request may race to load it
        if whisper_stt is None:
            whisper_stt = WhisperSTT()
    return whisper_stt
