import subprocess
import pathlib
import time
import os
import base64
import logging
import json
from typing import Optional, Dict, Tuple, Any, List

import requests

from backend import llm_models
from backend.config import settings as _settings
from backend.screen.ffmpeg_paths import ffmpeg_error_hint, resolve_ffmpeg_path

# ---------------------------------------------------------
# PATHS AND BASIC CONFIG
# ---------------------------------------------------------

HERE = pathlib.Path(__file__).resolve().parent
PROJECT_ROOT = HERE.parent  # .../backend
RECORDINGS_DIR = HERE / "recordings"
RECORDINGS_DIR.mkdir(parents=True, exist_ok=True)

SARAHVISION_EXE = HERE / "sarahvision.exe"

# FFMPEG for direct operations (frame extraction, etc.).
FFMPEG_PATH = resolve_ffmpeg_path(HERE)

logger = logging.getLogger("sarah.screen")


def _ensure_ffmpeg_exists() -> None:
    if not FFMPEG_PATH.exists():
        raise RuntimeError(ffmpeg_error_hint(FFMPEG_PATH))


def _run_sarahvision(args: list[str]) -> subprocess.CompletedProcess:
    """
    Run sarahvision.exe with the provided arguments and return the CompletedProcess.
    """
    if not SARAHVISION_EXE.exists():
        raise RuntimeError(
            f"[SarahVision] sarahvision.exe not found at: {SARAHVISION_EXE}"
        )

    cmd = [str(SARAHVISION_EXE), *args]
    proc = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    if proc.returncode != 0:
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

    _run_sarahvision(args)

    if not temp_jpg.exists():
        raise RuntimeError(
            "[SarahVision] screenshot did not produce an output file."
        )

    data = temp_jpg.read_bytes()

    try:
        temp_jpg.unlink()
    except OSError:
        pass

    return data


def _build_recording_name() -> pathlib.Path:
    ts = time.strftime("%Y%m%d_%H%M%S")
    return RECORDINGS_DIR / f"sarah_capture_{ts}.mp4"


def start_recording(
    monitor: int = 1,
    duration_sec: Optional[int] = None,
    size: str = "1920x1080",
    fps: int = 30,
) -> Dict[str, Any]:
    """
    Start a screen recording. If duration_sec is provided, sarahvision.exe will
    stop automatically after that many seconds; otherwise, it will record until
    stop_recording() is called.
    """
    _ensure_ffmpeg_exists()
    RECORDINGS_DIR.mkdir(parents=True, exist_ok=True)

    out_path = _build_recording_name()

    args = [
        "record",
        "--monitor",
        str(monitor),
        "--out",
        str(out_path),
        "--size",
        size,
        "--fps",
        str(fps),
    ]

    if duration_sec is not None:
        args.extend(["--duration", str(duration_sec)])

    try:
        subprocess.Popen(
            [str(SARAHVISION_EXE), *args],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"[SarahVision] failed to start recording: {exc}") from exc

    return {
        "path": str(out_path),
        "monitor": monitor,
        "duration_sec": duration_sec,
        "size": size,
        "fps": fps,
    }


def stop_recording() -> Dict[str, Any]:
    """
    Ask sarahvision.exe to stop the active recording (if any). The exe is
    responsible for tracking whatever it started.
    Returns information about the last recording if available.
    """
    try:
        proc = _run_sarahvision(["stop"])
    except RuntimeError as exc:
        return {
            "path": None,
            "status": "no_active_recording",
            "detail": str(exc),
        }

    last_path = None
    for line in (proc.stdout or "").splitlines():
        line = line.strip()
        if line.lower().startswith("saved:"):
            last_path = line.split(":", 1)[1].strip()

    return {
        "path": last_path,
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


def _extract_thumbnail_frame_bytes(video_path: pathlib.Path) -> bytes:
    """
    Use ffmpeg to grab a representative frame from the recording as JPEG bytes.
    We keep this simple and robust – if anything fails we raise so the caller
    can fall back cleanly.
    """
    if not FFMPEG_PATH.exists():
        raise FileNotFoundError(ffmpeg_error_hint(FFMPEG_PATH))

    thumb_path = RECORDINGS_DIR / "_thumb_frame.jpg"
    if thumb_path.exists():
        try:
            thumb_path.unlink()
        except OSError:
            pass

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
    proc = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if proc.returncode != 0 or not thumb_path.exists():
        err_tail = (proc.stderr or "")[-400:]
        raise RuntimeError(f"ffmpeg thumbnail extraction failed: {err_tail}")

    data = thumb_path.read_bytes()
    try:
        thumb_path.unlink()
    except OSError:
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

    model = llm_models.current_vision_model()
    image_b64 = base64.b64encode(image_bytes).decode("ascii")
    url = f"{_settings.openrouter_base_url}/chat/completions"

    payload = {
        "model": model,
        "reasoning": {"effort": "low", "exclude": True},
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
        "max_tokens": max(96, llm_models.VISION_MIN_COMPLETION_TOKENS),
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
    model = os.getenv("OLLAMA_VISION_MODEL", "llava")

    image_b64 = base64.b64encode(image_bytes).decode("ascii")
    url = f"{host}/api/generate"
    payload = {
        "model": model,
        "reasoning": {"effort": "low", "exclude": True},
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
    """
    try:
        if not path or not pathlib.Path(path).exists():
            return "Recording saved (no file path available for analysis)."

        video_path = pathlib.Path(path)
        frame_bytes = _extract_thumbnail_frame_bytes(video_path)

        summary: Optional[str] = None

        summary = _call_llm_vision(frame_bytes)

        if not summary:
            summary = _call_ollama_vision(frame_bytes)

        if not summary:
            return f"Recording saved to {video_path.name} (screen summary unavailable)."

        summary = summary.strip().replace("\n", " ")
        if len(summary) > 220:
            summary = summary[:219].rstrip() + "…"

        return summary
    except Exception as exc:  # noqa: BLE001
        logger.warning("summarize_recording failed: %s", exc)
        safe_name = pathlib.Path(path).name if path else "recording"
        return f"Recording saved to {safe_name} (analysis error)."


# ---------------------------------------------------------
# ULTRA-MODE SCREENSHOT + SNIPPET ANALYSIS
# ---------------------------------------------------------

def _call_llm_vision_structured(image_bytes: bytes) -> Optional[Dict[str, Any]]:
    """
    Ultra mode: ask cloud vision (OpenRouter) for a structured JSON analysis of a screenshot.
    Returns dict with keys: summary, emotion, emotion_intensity, raw_text, tags, has_code, has_error, error_text
    """
    api_key = _settings.openrouter_api_key or os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        return None

    model = llm_models.current_vision_model()
    image_b64 = base64.b64encode(image_bytes).decode("ascii")
    url = f"{_settings.openrouter_base_url}/chat/completions"

    system_prompt = (
        "You are SarahVision, an assistant who inspects screenshots for a developer. "
        "You must respond with STRICT JSON ONLY, no extra text. "
        "The JSON schema is:\n"
        "{\n"
        '  "summary": string,            // one short sentence (<= 30 words) describing the main content\n'
        '  "emotion": string,            // one of: "happy", "sad", "angry", "shy", "surprised", "neutral", "thinking"\n'
        '  "emotion_intensity": number,  // 0.0 to 1.0\n'
        '  "raw_text": string,           // any visible important text (errors, filenames, headings), concatenated\n'
        '  "has_code": boolean,          // whether code is visible\n'
        '  "has_error": boolean,         // whether an error / warning / alert is visible\n'
        '  "error_text": string          // if has_error=true, short text of the error or message\n'
        "}\n"
        "If unsure, make your best guess. DO NOT wrap JSON in markdown. DO NOT add comments."
    )

    user_text = (
        "Analyze this screenshot. Fill the JSON fields according to the schema. "
        "Focus on developer context: code editors, terminals, errors, stack traces, files, etc."
    )

    payload = {
        "model": model,
        "reasoning": {"effort": "low", "exclude": True},
        "response_format": {"type": "json_object"},
        "messages": [
            {
                "role": "system",
                "content": system_prompt,
            },
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": user_text},
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/jpeg;base64,{image_b64}"
                        },
                    },
                ],
            },
        ],
        "max_tokens": max(512, llm_models.VISION_MIN_COMPLETION_TOKENS),
        "temperature": 0.1,
    }

    try:
        resp = requests.post(
            url,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            timeout=60,
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
            raw = content.strip()
        elif isinstance(content, list):
            chunks: List[str] = []
            for part in content:
                if isinstance(part, dict):
                    t = part.get("text", "")
                    if t:
                        chunks.append(t)
            raw = " ".join(chunks).strip()
        else:
            return None

        if not raw:
            return None

        try:
            parsed = json.loads(raw)
            return parsed
        except Exception:
            # If the model returned something almost-JSON but not quite, try to salvage
            try:
                start = raw.find("{")
                end = raw.rfind("}")
                if start != -1 and end != -1 and end > start:
                    parsed = json.loads(raw[start : end + 1])
                    return parsed
            except Exception:
                pass

        return None
    except Exception as exc:  # noqa: BLE001
        logger.warning("LLM structured vision call failed: %s", exc)
        return None


def _infer_emotion_from_text(text: str) -> Tuple[str, float]:
    """
    Fallback heuristic emotion mapping from summary/error text.
    """
    lowered = (text or "").lower()

    if any(k in lowered for k in ["error", "exception", "failed", "crash", "stack trace", "traceback"]):
        return "sad", 0.7
    if any(k in lowered for k in ["warning", "deprecated", "caution"]):
        return "thinking", 0.5
    if any(k in lowered for k in ["success", "connected", "running", "ready"]):
        return "happy", 0.6
    if any(k in lowered for k in ["cute", "anime", "game", "win", "victory"]):
        return "happy", 0.8
    if any(k in lowered for k in ["angry", "rage", "wtf"]):
        return "angry", 0.7

    return "neutral", 0.3


def analyze_screenshot_bytes(image_bytes: bytes) -> Dict[str, Any]:
    """
    ULTRA mode: full analysis for a screenshot or snippet image.

    Returns dict:
    {
      "summary": str,
      "emotion": str,
      "emotion_intensity": float,
      "raw_text": str
    }
    """
    result: Dict[str, Any] = {
        "summary": None,
        "emotion": "neutral",
        "emotion_intensity": 0.3,
        "raw_text": "",
    }

    # 1) Try structured cloud vision first
    structured = _call_llm_vision_structured(image_bytes)
    if structured:
        summary = (structured.get("summary") or "").strip()
        emotion = (structured.get("emotion") or "neutral").strip().lower()
        intensity = structured.get("emotion_intensity")
        raw_text = (structured.get("raw_text") or "").strip()

        if not emotion:
            emotion, guessed_intensity = _infer_emotion_from_text(summary or raw_text)
            intensity = intensity if isinstance(intensity, (int, float)) else guessed_intensity

        if not isinstance(intensity, (int, float)):
            intensity = 0.3

        intensity = float(max(0.0, min(1.0, intensity)))

        result["summary"] = summary or "Screenshot analyzed (no clear summary)."
        result["emotion"] = emotion
        result["emotion_intensity"] = intensity
        result["raw_text"] = raw_text
        return result

    # 2) Fallback: simple LLM summary
    summary = _call_llm_vision(image_bytes)
    if not summary:
        summary = _call_ollama_vision(image_bytes)

    if not summary:
        result["summary"] = "Screenshot captured (analysis unavailable)."
        return result

    summary_clean = summary.strip().replace("\n", " ")
    if len(summary_clean) > 220:
        summary_clean = summary_clean[:219].rstrip() + "…"

    emo, inten = _infer_emotion_from_text(summary_clean)
    result["summary"] = summary_clean
    result["emotion"] = emo
    result["emotion_intensity"] = inten
    result["raw_text"] = ""
    return result
