import subprocess
import pathlib
import time
import os
import base64
import logging
from typing import Optional, Dict, Tuple

import requests

from backend.config import settings as _settings
from backend.screen.ffmpeg_paths import ffmpeg_error_hint, resolve_ffmpeg_path

# ---------------------------------------------------------
# PATHS AND BASIC CONFIG
# ---------------------------------------------------------

HERE = pathlib.Path(__file__).resolve().parent
PROJECT_ROOT = HERE.parent.parent  # .../backend

RECORDINGS_DIR = HERE / "recordings"
RECORDINGS_DIR.mkdir(parents=True, exist_ok=True)

SARAHVISION_EXE = HERE / "sarahvision.exe"

# FFMPEG for direct operations (frame extraction, etc.).
FFMPEG_PATH = resolve_ffmpeg_path(HERE)

logger = logging.getLogger("sarah.screen")
logger.setLevel(logging.INFO)


# ---------------------------------------------------------
# BASIC EXISTENCE CHECKS
# ---------------------------------------------------------

def _ensure_ffmpeg_exists() -> None:
    if not FFMPEG_PATH.exists():
        raise RuntimeError(ffmpeg_error_hint(FFMPEG_PATH))


def _ensure_sarahvision_exists() -> None:
    if not SARAHVISION_EXE.exists():
        raise RuntimeError(
            f"[SarahVision] sarahvision.exe not found at: {SARAHVISION_EXE}"
        )


# ---------------------------------------------------------
# WINDOWS-SAFE SARAHVISION EXECUTION
# ---------------------------------------------------------

def _run_sarahvision(args: list[str], timeout: int = 60) -> subprocess.CompletedProcess:
    """
    Run sarahvision.exe with the provided arguments and return the CompletedProcess.

    Ultra-mode fixes:
        - Enforces correct working directory (HERE)
        - Uses CREATE_NO_WINDOW if available (no annoying console popups)
        - Raises concise errors that bubble to FastAPI as 500s
        - Optional timeout safeguard
    """
    _ensure_sarahvision_exists()

    cmd = [str(SARAHVISION_EXE), *args]
    logger.info("[SarahVision] Running: %s", " ".join(cmd))

    creation_flags = 0
    if hasattr(subprocess, "CREATE_NO_WINDOW"):
        creation_flags |= subprocess.CREATE_NO_WINDOW

    try:
        proc = subprocess.run(
            cmd,
            cwd=str(HERE),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            shell=False,
            timeout=timeout,
            creationflags=creation_flags,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(
            f"[SarahVision] command timed out after {timeout}s: {exc}"
        ) from exc
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"[SarahVision] command failed to launch: {exc}") from exc

    if proc.returncode != 0:
        # Bubble up a concise but useful error
        raise RuntimeError(
            f"[SarahVision] command failed (exit {proc.returncode}):\n"
            f"STDOUT: {proc.stdout}\nSTDERR: {proc.stderr}"
        )

    return proc


# ---------------------------------------------------------
# SCREENSHOT / RECORDING HELPERS
# ---------------------------------------------------------

def grab_frame_jpeg(monitor: int = 1) -> bytes:
    """
    Capture a single JPEG frame from the target monitor using sarahvision.exe.
    Returns raw JPEG bytes on success.
    """
    _ensure_ffmpeg_exists()
    _ensure_sarahvision_exists()

    # Build a temporary path under recordings
    RECORDINGS_DIR.mkdir(parents=True, exist_ok=True)
    temp_jpg = RECORDINGS_DIR / "_live_frame.jpg"
    if temp_jpg.exists():
        try:
            temp_jpg.unlink()
        except OSError:
            pass

    args = [
        "screenshot",
        "--monitor",
        str(monitor),
        "--out",
        str(temp_jpg),
    ]

    _run_sarahvision(args, timeout=30)

    if not temp_jpg.exists():
        raise RuntimeError(
            "[SarahVision] screenshot did not produce an output file."
        )

    data = temp_jpg.read_bytes()

    # Best-effort cleanup
    try:
        temp_jpg.unlink()
    except OSError:
        pass

    return data


def _build_recording_name() -> pathlib.Path:
    ts = time.strftime("%Y%m%d_%H%M%S")
    return RECORDINGS_DIR / f"sarah_capture_{ts}.mp4"


def start_recording(monitor: int = 1, duration_sec: Optional[int] = None) -> Dict:
    """
    Start a screen recording. If duration_sec is provided, sarahvision.exe will
    stop automatically after that many seconds; otherwise, it will record until
    stop_recording() is called.
    """
    _ensure_ffmpeg_exists()
    _ensure_sarahvision_exists()
    RECORDINGS_DIR.mkdir(parents=True, exist_ok=True)

    out_path = _build_recording_name()

    args = [
        "record",
        "--monitor",
        str(monitor),
        "--out",
        str(out_path),
    ]

    if duration_sec is not None:
        args.extend(["--duration", str(duration_sec)])

    creation_flags = 0
    if hasattr(subprocess, "CREATE_NO_WINDOW"):
        creation_flags |= subprocess.CREATE_NO_WINDOW

    # Non-blocking: sarahvision.exe handles the recording lifecycle
    try:
        subprocess.Popen(
            [str(SARAHVISION_EXE), *args],
            cwd=str(HERE),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            shell=False,
            creationflags=creation_flags,
        )
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"[SarahVision] failed to start recording: {exc}") from exc

    return {
        "recording_path": str(out_path),
        "monitor": monitor,
        "duration_sec": duration_sec,
    }


def stop_recording() -> Dict:
    """
    Ask sarahvision.exe to stop the active recording (if any). The exe is
    responsible for tracking whatever it started.
    Returns information about the last recording if available.
    """
    try:
        proc = _run_sarahvision(["stop"], timeout=30)
    except RuntimeError as exc:
        # No active recording, or stop failed – surface a clean payload
        return {
            "recording_path": None,
            "status": "no_active_recording",
            "detail": str(exc),
        }

    # Very simple protocol: sarahvision.exe prints the final path on stdout.
    last_path = None
    for line in (proc.stdout or "").splitlines():
        line = line.strip()
        if line.lower().startswith("saved:"):
            last_path = line.split(":", 1)[1].strip()

    return {
        "recording_path": last_path,
        "status": "stopped",
        "raw_stdout": proc.stdout,
    }


def get_last_recording() -> Optional[pathlib.Path]:
    """
    Return the most recent sarah_capture_*.mp4 file in RECORDINGS_DIR or None
    if nothing exists yet.
    """
    if not RECORDINGS_DIR.exists():
        return None

    mp4s = sorted(RECORDINGS_DIR.glob("sarah_capture_*.mp4"))
    if not mp4s:
        return None
    return mp4s[-1]


# ---------------------------------------------------------
# VISION-BASED AUTO SUMMARY OF RECORDINGS
# ---------------------------------------------------------

def _detect_gpu_accel_flags() -> Tuple[list[str], str]:
    """
    Ultra-mode helper: returns ffmpeg flags for GPU acceleration if available.

    This is intentionally conservative:
      - checks env override SARAHVISION_FFMPEG_GPU
      - otherwise tries NVENC flags on Windows/NVIDIA setups
    """
    override = os.getenv("SARAHVISION_FFMPEG_GPU", "").strip().lower()
    if override == "off":
        return [], "gpu_off"

    if override == "nvenc":
        return ["-hwaccel", "cuda"], "override_nvenc"

    # Heuristic: if on Windows, assume optional NVENC could exist.
    if os.name == "nt":
        return ["-hwaccel", "cuda"], "heuristic_nvenc"

    # Fallback: CPU only
    return [], "cpu"


logger = logging.getLogger("sarah.screen")


def _extract_thumbnail_frame_bytes(video_path: pathlib.Path) -> bytes:
    """
    Use ffmpeg to grab a representative frame from the recording as JPEG bytes.
    We keep this simple and robust – if anything fails we raise so the caller
    can fall back cleanly.

    Ultra-mode:
      - attempts optional GPU acceleration
      - better error-tail reporting
      - cleans up temp files safely
    """
    _ensure_ffmpeg_exists()

    thumb_path = RECORDINGS_DIR / "_thumb_frame.jpg"
    if thumb_path.exists():
        try:
            thumb_path.unlink()
        except OSError:
            pass

    gpu_flags, gpu_mode = _detect_gpu_accel_flags()
    if gpu_mode != "cpu":
        logger.info("[SarahVision] ffmpeg GPU mode: %s (%s)", gpu_mode, " ".join(gpu_flags))

    # Grab a frame ~3 seconds into the video (or the first frame if shorter).
    cmd = [
        str(FFMPEG_PATH),
        "-y",
        "-ss",
        "3",
        "-i",
        str(video_path),
        "-frames:v",
        "1",
        "-q:v",
        "2",
        str(thumb_path),
    ]

    # Insert GPU flags right after ffmpeg binary if we have them
    if gpu_flags:
        cmd = [str(FFMPEG_PATH), *gpu_flags] + cmd[1:]

    logger.info("[SarahVision] ffmpeg thumbnail cmd: %s", " ".join(cmd))

    try:
        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=45,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"ffmpeg thumbnail extraction timed out: {exc}") from exc

    if proc.returncode != 0 or not thumb_path.exists():
        err_tail = (proc.stderr or "")[-400:]
        raise RuntimeError(f"ffmpeg thumbnail extraction failed: {err_tail}")

    data = thumb_path.read_bytes()
    try:
        thumb_path.unlink()
    except OSError:
        # Non-fatal if cleanup fails
        pass
    return data


def _call_llm_vision(image_bytes: bytes) -> Optional[str]:
    """
    Try to use cloud vision via OpenRouter (if SARAH_OPENROUTER_API_KEY is set) to summarize the frame.
    Returns a short, human-readable summary string or None on failure.
    """
    api_key = _settings.openrouter_api_key or os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        return None

    model = _settings.openrouter_model
    image_b64 = base64.b64encode(image_bytes).decode("ascii")
    url = f"{_settings.openrouter_base_url}/chat/completions"

    payload = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": (
                            "You are an assistant named Sarah. "
                            "Look at this video frame from the user's screen "
                            "and describe, in ONE very short sentence (max 30 words), "
                            "what is most important or central in the scene. "
                            "Be concrete, not generic."
                        ),
                    },
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/jpeg;base64,{image_b64}"
                        },
                    },
                ],
            }
        ],
        "max_tokens": 96,
        "temperature": 0.2,
    }

    try:
        resp = requests.post(
            url,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            timeout=45,
            json=payload,
        )
        resp.raise_for_status()
        data = resp.json()
        choices = data.get("choices") or []
        if not choices:
            return None
        msg = choices[0].get("message") or {}
        content = msg.get("content")
        if isinstance(content, str):
            return content.strip()
        # OpenAI-style: content can be a list of {type,text}
        if isinstance(content, list):
            text_chunks = [
                part.get("text", "")
                for part in content
                if isinstance(part, dict)
            ]
            combined = " ".join(text_chunks).strip()
            return combined or None
        return None
    except Exception as exc:  # noqa: BLE001
        logger.warning("LLM vision call failed: %s", exc)
        return None


def _call_ollama_vision(image_bytes: bytes) -> Optional[str]:
    """
    Try to use a local Ollama vision model as a fallback.
    This assumes Ollama is running locally (default http://127.0.0.1:11434).
    """
    host = os.getenv("OLLAMA_HOST", _settings.ollama_base_url).rstrip("/")
    model = os.getenv("OLLAMA_VISION_MODEL", "llava")  # creator can change

    image_b64 = base64.b64encode(image_bytes).decode("ascii")
    url = f"{host}/api/generate"
    payload = {
        "model": model,
        "prompt": (
            "You are an assistant named Sarah. "
            "Look at this screenshot from a screen recording and describe, "
            "in ONE very short sentence (max 30 words), what is happening."
        ),
        "images": [image_b64],
        "stream": False,
    }

    try:
        resp = requests.post(url, json=payload, timeout=60)
        resp.raise_for_status()
        data = resp.json()
        text = (data.get("response") or "").strip()
        return text or None
    except Exception as exc:  # noqa: BLE001
        logger.warning("Ollama vision call failed: %s", exc)
        return None


def summarize_recording(path: pathlib.Path) -> str:
    """
    Produce a *very* brief, Sarah-style summary of what appears in the
    last recording by:
      1. Extracting a thumbnail frame with ffmpeg.
      2. Trying cloud vision (OpenRouter) first (if configured).
      3. Falling back to a local Ollama vision model (if running).
      4. If everything fails, returning a safe fallback string.

    The frontend currently displays this as a one-line summary and also
    injects it into the Sarah chat as a 'screen summary' message.
    """
    try:
        if not path or not pathlib.Path(path).exists():
            return "Recording saved (no file path available for analysis)."

        video_path = pathlib.Path(path)
        frame_bytes = _extract_thumbnail_frame_bytes(video_path)

        summary: Optional[str] = None

        # 1) Try cloud vision via OpenRouter
        summary = _call_llm_vision(frame_bytes)

        # 2) Fallback to Ollama vision if needed
        if not summary:
            summary = _call_ollama_vision(frame_bytes)

        if not summary:
            return f"Recording saved to {video_path.name} (screen summary unavailable)."

        # Keep it compact for the UI
        summary = summary.strip().replace("\n", " ")
        if len(summary) > 220:
            summary = summary[:219].rstrip() + "…"

        return summary
    except Exception as exc:  # noqa: BLE001
        logger.warning("summarize_recording failed: %s", exc)
        # Never break the app just because analysis failed
        safe_name = pathlib.Path(path).name if path else "recording"
        return f"Recording saved to {safe_name} (analysis error)."
