from __future__ import annotations
from typing import Optional

import os
# Make sure console/log output doesn't explode on Windows
os.environ["PYTHONIOENCODING"] = "utf-8"
os.environ["PYTHONLEGACYWINDOWSSTDIO"] = "utf-8"

import sys, os
sys.path.append(os.path.dirname(os.path.dirname(__file__)))

import sys
import asyncio
import subprocess
from pathlib import Path
from typing import Optional, Dict, Any, List

import logging
import base64
import requests
import json

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, Response
from pydantic import BaseModel
from backend.whisper_stt import get_whisper_stt

# ⭐ SQL imports
from backend.db import get_connection  
from backend.db import init_db
from backend.models_sql import (
    get_setting,
    set_setting,
    get_all_settings,
    add_memory,
    get_memories,
    search_memories,
    get_pinned_memories,      
    set_memory_pinned,        
    create_conversation,
    list_conversations,
    rename_conversation,      
    add_message,
    get_messages,
    register_skill,
    get_all_skills,
    set_skill_enabled,
    add_log,
    get_logs,
)

CURRENT_DIR = Path(__file__).resolve().parent
BACKEND_ROOT = CURRENT_DIR
PROJECT_ROOT = BACKEND_ROOT.parent

sys.path.insert(0, str(BACKEND_ROOT))

SarahCore = None
SCREEN_ENABLED = False
SCREEN_IMPORT_ERROR: Optional[str] = None

try:
    from backend.sarah_core.sarah_core import SarahCore
except Exception as e:
    print("[ERROR] Failed to import SarahCore:", e)

try:
    from piper.piper_tts import piper_tts  # type: ignore
except Exception as e:
    print("[ERROR] Piper TTS import failed:", e)
    piper_tts = None

try:
    from backend.screen.screen_capture import (
    start_recording,
    stop_recording,
    get_last_screenshot_info,
    )
    SCREEN_ENABLED = True
except Exception as e:
    SCREEN_ENABLED = False
    SCREEN_IMPORT_ERROR = str(e)
    start_recording = stop_recording = get_last_screenshot_info = None

app = FastAPI(
    title="Sarah AI V11 Backend",
    description="Backend for Sarah AI V11 (chat, TTS, screen capture, Ultra Vision).",
    version="11.0.0",
)

# -------------------------------------------------------------------
# LLM GLOBALS
# -------------------------------------------------------------------
LLM_MODE: str = os.getenv("SARAH_LLM_MODE", "online").lower()
LOCAL_LLM_MODEL: str = os.getenv("SARAH_LOCAL_MODEL", "dolphin-mixtral")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://127.0.0.1:8908",
        "http://localhost:8908",
        "http://127.0.0.1:8909",
        "http://localhost:8909",
        "http://127.0.0.1:3000",
        "http://localhost:3000",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
# -------------------------------------------------------------------
# Pydantic models
# -------------------------------------------------------------------
class AudioChunk(BaseModel):
    audio_b64: str
    mime_type: str | None = "audio/webm"


class ChatRequest(BaseModel):
    message: str
    from_creator: bool = True
    conversation_id: Optional[int] = None  # ⭐ optional SQL convo linking


class ChatResponse(BaseModel):
    ok: bool
    reply: str
    emotion: Optional[str] = None
    emotion_intensity: Optional[float] = None
    affinity_to_creator: Optional[float] = None
    error: Optional[str] = None


class TTSRequest(BaseModel):
    text: str
    length_scale: Optional[float] = 1.0
    noise_scale: Optional[float] = 0.6
    noise_w: Optional[float] = 0.8
    voice: Optional[str] = None


class ScreenRecordingStartResponse(BaseModel):
    ok: bool
    error: Optional[str] = None


class ScreenRecordingStopResponse(BaseModel):
    ok: bool
    error: Optional[str] = None
    recording_path: Optional[str] = None


class ScreenshotAnalysisResponse(BaseModel):
    ok: bool
    error: Optional[str] = None
    description: Optional[str] = None
    suggestions: Optional[str] = None


# ⭐ SQL models
class SettingUpdate(BaseModel):
    key: str
    value: str


class ConversationCreate(BaseModel):
    title: Optional[str] = None


class MessageCreate(BaseModel):
    role: str  # 'user' | 'assistant' | 'system'
    content: str
    meta_json: Optional[Dict[str, Any]] = None


class SkillRegister(BaseModel):
    name: str
    slug: str
    description: Optional[str] = ""
    enabled: Optional[bool] = True
    config: Optional[Dict[str, Any]] = None


class STTRequest(BaseModel):
    audio_b64: str
    mime_type: Optional[str] = "audio/webm"


_sarah: Optional["SarahCore"] = None

# -------------------------------------------------------------------
# SarahCore loader (now shows real import errors)
# -------------------------------------------------------------------
def get_sarah() -> "SarahCore":
    global _sarah

    # Return existing instance
    if _sarah is not None:
        return _sarah

    # Try importing SarahCore and report REAL traceback if it fails
    try:
        from backend.sarah_core.sarah_core import SarahCore
    except Exception as e:
        import traceback
        print("\n=========== SARAHCORE IMPORT ERROR ===========")
        traceback.print_exc()
        print("==============================================\n")
        raise RuntimeError(f"SarahCore crashed during import: {e}")

    if SarahCore is None:
        raise RuntimeError("SarahCore imported as None; cannot handle chat.")

    print("[SARAH INIT] Initializing SarahCore...")

    try:
        _sarah = SarahCore()
    except Exception as e:
        import traceback
        print("\n=========== SARAHCORE INSTANTIATION ERROR ===========")
        traceback.print_exc()
        print("=====================================================\n")
        raise RuntimeError(f"SarahCore failed to initialize: {e}")

    print(
        "[SARAH INIT] SarahCore initialized. Core version:",
        getattr(_sarah, "core_version", "unknown"),
    )

    # Apply initial LLM mode
    try:
        if hasattr(_sarah, "set_llm_mode"):
            _sarah.set_llm_mode(LLM_MODE, LOCAL_LLM_MODEL)
    except Exception as e:
        logging.warning(f"[LLM MODE] Could not set initial mode: {e}")

    return _sarah

# PRELOAD SARAHCORE TO PREVENT TOGGLE ERRORS
try:
    _ = get_sarah()
    print("[BACKEND] SarahCore preloaded.")
except Exception as e:
    print("[BACKEND] Could not preload SarahCore:", e)


# -------------------------------------------------------------------
# ⭐ STARTUP — INIT SQL DATABASE
# -------------------------------------------------------------------

@app.on_event("startup")
async def startup_event():
    print("[INIT] Booting SQL database...")
    init_db()
    print("[INIT] SQL ready.")


# -------------------------------------------------------------------
# HEALTH
# -------------------------------------------------------------------

@app.get("/api/health")
async def api_health():
    info: Dict[str, Any] = {
        "ok": True,
        "screen_enabled": SCREEN_ENABLED,
        "screen_error": SCREEN_IMPORT_ERROR,
    }

    try:
        sarah = get_sarah()
        info["sarah_version"] = getattr(sarah, "core_version", "unknown")
        info["llm_mode"] = LLM_MODE
    except Exception as e:
        info["ok"] = False
        info["error"] = str(e)

    return info


# -------------------------------------------------------------------
# ⭐ SQL SETTINGS ENDPOINTS
# -------------------------------------------------------------------

@app.get("/api/settings")
def api_get_settings():
    return get_all_settings()


@app.get("/api/settings/{key}")
def api_get_setting(key: str):
    return {"key": key, "value": get_setting(key)}


@app.post("/api/settings")
def api_set_setting(payload: SettingUpdate):
    set_setting(payload.key, payload.value)
    return {"ok": True, "key": payload.key, "value": payload.value}


# -------------------------------------------------------------------
# ⭐ LLM MODE TOGGLE — GET + POST
# -------------------------------------------------------------------

@app.get("/api/llm_mode")
def api_get_llm_mode():
    return {
        "mode": LLM_MODE,
        "local_model": LOCAL_LLM_MODEL,
    }


@app.post("/api/llm_mode")
def api_set_llm_mode(payload: Dict[str, str]):
    global LLM_MODE, LOCAL_LLM_MODEL

    mode = (payload.get("mode") or "").lower()
    local_model = payload.get("local_model") or LOCAL_LLM_MODEL

    if mode not in ("online", "local"):
        raise HTTPException(status_code=400, detail="Invalid LLM mode.")

    LLM_MODE = mode
    LOCAL_LLM_MODEL = local_model

    os.environ["SARAH_LLM_MODE"] = LLM_MODE
    os.environ["SARAH_LOCAL_MODEL"] = LOCAL_LLM_MODEL

    print(f"[LLM MODE] Changing mode to {LLM_MODE} (local_model={LOCAL_LLM_MODEL})")

    try:
        sarah = get_sarah()
        if sarah:
            sarah.set_llm_mode(LLM_MODE, LOCAL_LLM_MODEL)
            print(
                f"[LLM MODE] SarahCore now using mode={LLM_MODE}, local_model={LOCAL_LLM_MODEL}"
            )
        else:
            print("[LLM MODE] SarahCore not initialized yet.")
    except Exception as e:
        logging.warning(f"[LLM MODE] Failed to update SarahCore: {e}")

    return {
        "ok": True,
        "mode": LLM_MODE,
        "local_model": LOCAL_LLM_MODEL,
    }


# -------------------------------------------------------------------
# ⭐ SQL CONVERSATIONS / HISTORY
# -------------------------------------------------------------------

@app.get("/api/conversations")
def api_list_conversations():
    return {"ok": True, "conversations": list_conversations()}


@app.post("/api/conversations")
def api_create_conversation(payload: ConversationCreate):
    conv_id = create_conversation(payload.title)
    return {"ok": True, "conversation_id": conv_id}


@app.get("/api/conversations/{conversation_id}/messages")
def api_get_conversation_messages(conversation_id: int, limit: int = 200):
    msgs = get_messages(conversation_id, limit=limit)
    return {"ok": True, "messages": msgs}


@app.post("/api/conversations/{conversation_id}/messages")
def api_add_message(conversation_id: int, payload: MessageCreate):
    meta_json_str = json.dumps(payload.meta_json) if payload.meta_json else None
    add_message(conversation_id, payload.role, payload.content, meta_json_str)
    return {"ok": True}

class ConversationRename(BaseModel):
    title: str


@app.post("/api/conversations/{conversation_id}/title")
def api_rename_conversation(conversation_id: int, payload: ConversationRename):
    try:
        rename_conversation(conversation_id, payload.title)
        return {"ok": True}
    except Exception as e:
        logging.exception("[/api/conversations/{id}/title] failed")
        raise HTTPException(status_code=500, detail=str(e))
    
@app.delete("/api/conversations/{conversation_id}")
def api_delete_conversation(conversation_id: int):
    try:
        conn = get_connection()
        cur = conn.cursor()

        # Delete all messages linked to the conversation
        cur.execute(
            "DELETE FROM messages WHERE conversation_id = ?",
            (conversation_id,),
        )

        # Delete the conversation
        cur.execute(
            "DELETE FROM conversations WHERE id = ?",
            (conversation_id,),
        )

        conn.commit()
        conn.close()

        return {"ok": True, "deleted_id": conversation_id}

    except Exception as e:
        logging.exception("[DELETE /api/conversations/{id}] failed")
        raise HTTPException(status_code=500, detail=str(e))

# -------------------------------------------------------------------
# ⭐ SQL LOGS
# -------------------------------------------------------------------
@app.get("/api/logs")
def api_get_logs(limit: int = 200):
    logs = get_logs(limit=limit)
    return {"ok": True, "logs": logs}


# -------------------------------------------------------------------
# ⭐ SQL SKILLS / PLUGINS
# -------------------------------------------------------------------

@app.get("/api/skills")
def api_get_skills():
    return {"ok": True, "skills": get_all_skills()}


@app.post("/api/skills/register")
def api_register_skill(payload: SkillRegister):
    config_json = json.dumps(payload.config or {})
    register_skill(
        payload.name,
        payload.slug,
        payload.description or "",
        bool(payload.enabled if payload.enabled is not None else True),
        config_json,
    )
    return {"ok": True}


@app.post("/api/skills/{slug}/enable")
def api_enable_skill(slug: str):
    set_skill_enabled(slug, True)
    return {"ok": True, "slug": slug, "enabled": True}


@app.post("/api/skills/{slug}/disable")
def api_disable_skill(slug: str):
    set_skill_enabled(slug, False)
    return {"ok": True, "slug": slug, "enabled": False}

# -------------------------------------------------------------------
# ⭐ SQL MEMORIES (browser + pinned)
# -------------------------------------------------------------------

@app.get("/api/memories")
def api_get_memories(limit: int = 50, keyword: str | None = None):
    try:
        if keyword:
            mems = search_memories(keyword)
        else:
            mems = get_memories(limit=limit)
        return {"ok": True, "memories": mems}
    except Exception as e:
        logging.exception("[/api/memories] failed")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/memories/pinned")
def api_get_pinned_memories(limit: int = 100):
    try:
        mems = get_pinned_memories(limit=limit)
        return {"ok": True, "memories": mems}
    except Exception as e:
        logging.exception("[/api/memories/pinned] failed")
        raise HTTPException(status_code=500, detail=str(e))


class MemoryCreate(BaseModel):
    role: str
    content: str
    tags: str | None = ""
    importance: int | None = 0


@app.post("/api/memories")
def api_add_memory_manual(payload: MemoryCreate):
    try:
        add_memory(
            role=payload.role,
            content=payload.content,
            tags=payload.tags or "",
            importance=payload.importance or 0,
        )
        return {"ok": True}
    except Exception as e:
        logging.exception("[/api/memories POST] failed")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/memories/{memory_id}/pin")
def api_pin_memory(memory_id: int):
    try:
        set_memory_pinned(memory_id, True)
        return {"ok": True, "pinned": True, "id": memory_id}
    except Exception as e:
        logging.exception("[/api/memories/{id}/pin] failed")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/memories/{memory_id}/unpin")
def api_unpin_memory(memory_id: int):
    try:
        set_memory_pinned(memory_id, False)
        return {"ok": True, "pinned": False, "id": memory_id}
    except Exception as e:
        logging.exception("[/api/memories/{id}/unpin] failed")
        raise HTTPException(status_code=500, detail=str(e))

# -------------------------------------------------------------------
# CHAT / TTS
# -------------------------------------------------------------------

@app.post("/api/chat", response_model=ChatResponse)
async def api_chat(payload: ChatRequest):
    sarah = get_sarah()

    conversation_id = payload.conversation_id

    # Optional: log user message to SQL
    if conversation_id is not None:
        try:
            add_message(conversation_id, "user", payload.message, None)
        except Exception as e:
            logging.warning(f"[SQL] Failed to save user message: {e}")

    try:
        result = await sarah.handle_message(
            message=payload.message,
            from_creator=payload.from_creator,
        )
    except Exception as e:
        logging.exception("[/api/chat] SarahCore.handle_message failed")
        # Optional SQL log
        try:
            add_log(
                "ERROR",
                "chat_failed",
                source="chat",
                payload_json=json.dumps({"error": str(e)}) if str(e) else None,
            )
        except Exception:
            pass
        raise HTTPException(status_code=500, detail=str(e))

    reply = getattr(result, "reply", "...I'm here, Creator.")
    emotion = getattr(result, "emotion", "neutral")
    emotion_intensity = getattr(result, "emotion_intensity", 0.0)
    affinity = getattr(result, "affinity_to_creator", 0.0)

    # Optional: log assistant message to SQL
    if conversation_id is not None:
        try:
            add_message(conversation_id, "assistant", reply, None)
        except Exception as e:
            logging.warning(f"[SQL] Failed to save assistant message: {e}")

    # Log the chat event to SQL logs (non-fatal if it fails)
    try:
        add_log(
            "INFO",
            "chat_message",
            source="chat",
            payload_json=json.dumps(
                {
                    "conversation_id": conversation_id,
                    "from_creator": payload.from_creator,
                    "input_len": len(payload.message),
                    "reply_len": len(reply),
                }
            ),
        )
    except Exception:
        pass

    return ChatResponse(
        ok=True,
        reply=reply,
        emotion=emotion,
        emotion_intensity=float(emotion_intensity or 0.0),
        affinity_to_creator=float(affinity or 0.0),
    )


@app.post("/api/tts")
async def api_tts(req: TTSRequest):

    if piper_tts is None:
        raise HTTPException(status_code=500, detail="Piper TTS is not available.")

    text = req.text.strip()
    if not text:
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
        raise HTTPException(status_code=500, detail=str(e))

    return Response(
        content=wav_bytes,
        media_type="audio/wav",
        headers= {"Content-Disposition": 'inline; filename=\"sarah_tts.wav\"'},
    )


# -------------------------------------------------------------------
# SCREEN / VISION
# -------------------------------------------------------------------

@app.post("/api/screen/start", response_model=ScreenRecordingStartResponse)
def api_screen_start():
    if not SCREEN_ENABLED or start_recording is None:
        return ScreenRecordingStartResponse(
            ok=False, error=f"Screen capture is disabled: {SCREEN_IMPORT_ERROR}"
        )

    try:
        start_recording()
        return ScreenRecordingStartResponse(ok=True)
    except Exception as e:
        return ScreenRecordingStartResponse(ok=False, error=str(e))


@app.post("/api/screen/stop", response_model=ScreenRecordingStopResponse)
def api_screen_stop():
    if not SCREEN_ENABLED or stop_recording is None:
        return ScreenRecordingStopResponse(
            ok=False, error=f"Screen capture is disabled: {SCREEN_IMPORT_ERROR}"
        )

    try:
        info = stop_recording()
        recording_path = info.get("recording_path") if info else None
        return ScreenRecordingStopResponse(ok=True, recording_path=recording_path)
    except Exception as e:
        return ScreenRecordingStopResponse(ok=False, error=str(e))


@app.get("/api/screen/analyze_last", response_model=ScreenshotAnalysisResponse)
def api_screen_analyze_last():

    if not SCREEN_ENABLED or get_last_screenshot_info is None:
        return ScreenshotAnalysisResponse(
            ok=False, error=f"Screen capture is disabled: {SCREEN_IMPORT_ERROR}"
        )

    info = get_last_screenshot_info()
    if not info or not info.get("image_b64"):
        return ScreenshotAnalysisResponse(
            ok=False, error="No screenshot data found."
        )

    try:
        return ScreenshotAnalysisResponse(
            ok=True,
            description=info.get("description"),
            suggestions=info.get("suggestions"),
        )
    except Exception as e:
        return ScreenshotAnalysisResponse(ok=False, error=str(e))


# -------------------------------------------------------------------
# SPEECH-TO-TEXT (Whisper) — base64 → temp file
# -------------------------------------------------------------------

@app.post("/api/stt_legacy")
async def api_stt_legacy(payload: STTRequest):
    """
    Legacy STT path kept for compatibility.
    Accept WebM -> Base64 audio, decode it, run Whisper STT,
    return the recognized text.
    """
    try:
        stt = get_whisper_stt()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Whisper load failed: {e}")

    try:
        audio_bytes = base64.b64decode(payload.audio_b64)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid base64: {e}")

    temp_path = CURRENT_DIR / "temp_stt.webm"
    try:
        temp_path.write_bytes(audio_bytes)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"File write failed: {e}")

    try:
        text = stt(str(temp_path))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"STT failed: {e}")
    finally:
        try:
            temp_path.unlink(missing_ok=True)
        except Exception:
            pass

    return {"ok": True, "text": text.strip() if text else ""}


# -------------------------------------------------------------------
# SPEECH-TO-TEXT (Whisper) — preferred helper path
# -------------------------------------------------------------------

class STTRequest2(BaseModel):
    audio_b64: str
    mime_type: str | None = "audio/webm"


@app.post("/api/stt")
async def api_stt(payload: STTRequest2):
    stt = get_whisper_stt()

    result = stt.stt_from_base64(payload.audio_b64, payload.mime_type)

    if not result.get("ok"):
        raise HTTPException(status_code=500, detail=result.get("error", "stt_failed"))

    return {"ok": True, "text": result.get("text", "")}


# -------------------------------------------------------------------
# ⭐ WAKE WORD ENGINE (VOSK) — WASAPI HEADSET MODE (sounddevice)
# -------------------------------------------------------------------
import threading
import queue
import numpy as np
from vosk import Model, KaldiRecognizer
import sounddevice as sd

WAKE_MODEL_PATH = r"E:\AI\SARAH_AI_V8\SARAH_AI_V8_Backend_Full\backend\piper\models\vosk-model-small-en-us-0.15"
WAKE_WORDS = ["hey sarah", "ok sarah"]
wake_events = queue.Queue()


def wake_word_listener():
    """
    Capture audio from the default input device (your Stealth 600 headset)
    using WASAPI/shared mode at 48 kHz float32, then downsample to 16 kHz
    int16 for Vosk.
    """

    try:
        print("[WAKE] Initializing Vosk wake model...")
        model = Model(WAKE_MODEL_PATH)
        recognizer = KaldiRecognizer(model, 16000)

        audio_q: "queue.Queue[bytes]" = queue.Queue()

        def audio_callback(indata, frames, time_info, status):
            # indata: float32, shape (frames, channels), samplerate = 48000
            if status:
                print(f"[WAKE] Audio status: {status}")

            # Flatten to mono
            data = indata[:, 0].copy()  # (frames,) float32, -1..1

            # Downsample 48000 → 16000 by taking every 3rd sample
            downsampled = data[::3]

            # Convert to 16-bit PCM bytes for Vosk
            pcm16 = (
                (downsampled * 32767.0)
                .clip(-32768, 32767)
                .astype(np.int16)
                .tobytes()
            )

            audio_q.put(pcm16)

        # Open shared-mode WASAPI input at 48 kHz, mono, float32
        stream = sd.InputStream(
            samplerate=48000,
            channels=1,
            dtype="float32",
            callback=audio_callback,
        )

        print("[WAKE] Opening WASAPI input stream (headset mic)...")
        stream.start()
        print("[WAKE] Wake-word engine started (WASAPI headset mode).")

        while True:
            data = audio_q.get()

            if recognizer.AcceptWaveform(data):
                result = json.loads(recognizer.Result())
                text = (result.get("text") or "").lower().strip()

                if text:
                    print(f"[WAKE DEBUG] Final: {text}")

                for w in WAKE_WORDS:
                    if w in text:
                        print("[WAKE] Wake word detected!")
                        wake_events.put("wake")
                        break
            else:
                partial = json.loads(recognizer.PartialResult()).get("partial", "")
                partial = partial.lower().strip()
                if partial:
                    print(f"[WAKE DEBUG] Partial: {partial}")

    except Exception as e:
        print("[WAKE] ERROR in wake-word thread:", e)


wake_thread = threading.Thread(target=wake_word_listener, daemon=True)
wake_thread.start()


@app.get("/api/wake")
def api_wake():
    if not wake_events.empty():
        wake_events.get()
        return {"wake": True}
    return {"wake": False}


# -------------------------------------------------------------------
# DEV ENTRY
# -------------------------------------------------------------------
def run():
    import uvicorn

    uvicorn.run(
        "server:app",
        host="127.0.0.1",
        port=int(os.getenv("PY_PORT", "8907")),
        reload=False,
        log_level="debug",
    )


if __name__ == "__main__":
    run()
