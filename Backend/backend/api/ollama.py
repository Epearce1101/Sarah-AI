"""Ollama vision + text analysis endpoints."""
from __future__ import annotations

import asyncio
import time
import traceback
from typing import Optional

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from backend import llm_models
from backend.api.schemas import OllamaTextAnalysisRequest
from backend.config import settings as _settings
from backend.services.vision_client import get_ollama_vision
import logging

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/api/ollama/health")
def api_ollama_health():
    """Check if Ollama is running and model is available."""
    try:
        ollama = get_ollama_vision()
        result = ollama.check_connection()
        return result
    except Exception as e:
        logger.error(f"[Ollama Health] Exception: {e}")
        return {"ok": False, "available": False, "error": str(e)}


@router.post("/api/ollama/warmup")
def api_ollama_warmup():
    """Warm up the Ollama vision model (loads it into memory)."""
    try:
        ollama = get_ollama_vision()
        success = ollama.warmup()
        return {"ok": success}
    except Exception as e:
        logger.error(f"[Ollama Warmup] Exception: {e}")
        return {"ok": False, "error": str(e)}


@router.post("/api/ollama/analyze")
async def api_ollama_analyze_image(
    file: UploadFile = File(...),
    mode: str = Form("general"),
    model: str = Form(_settings.ollama_vision_model),
    custom_prompt: Optional[str] = Form(None),
):
    """Analyze an image using local Ollama vision model (multipart upload)."""
    logger.debug(f"[Ollama Vision] Received upload - filename: {file.filename}, "
        f"content_type: {file.content_type}")
    logger.debug(f"[Ollama Vision] Mode: {mode}, Model: {model}")

    try:
        allowed_types = ["image/png", "image/jpeg", "image/jpg", "image/webp"]
        if file.content_type not in allowed_types:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid file type '{file.content_type}'. Allowed: {', '.join(allowed_types)}",
            )

        image_bytes = await file.read()
        file_size_mb = len(image_bytes) / (1024 * 1024)
        logger.debug(f"[Ollama Vision] Image size: {file_size_mb:.2f} MB")

        if len(image_bytes) > 15 * 1024 * 1024:
            raise HTTPException(
                status_code=400,
                detail=f"File too large ({file_size_mb:.2f} MB). Maximum: 15 MB",
            )

        ollama = get_ollama_vision(model=model)
        # Vision inference takes seconds; keep it off the event loop.
        result = await asyncio.to_thread(
            ollama.analyze_image,
            image_data=image_bytes, mode=mode, custom_prompt=custom_prompt,
        )

        if not result.get("ok"):
            error_detail = result.get("error", "Vision analysis failed")
            logger.error(f"[Ollama Vision] Analysis failed: {error_detail}")
            raise HTTPException(status_code=500, detail=error_detail)

        logger.debug(f"[Ollama Vision] Analysis successful - {result['timing_ms']}ms")
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[Ollama Vision] Exception: {e}")
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


def _analyze_text_openrouter(prompt: str, mode: str, start_time: float) -> dict:
    from openai import OpenAI

    client = OpenAI(api_key=_settings.openrouter_api_key, base_url=_settings.openrouter_base_url)
    resp = client.chat.completions.create(
        messages=[{"role": "user", "content": prompt}],
        max_tokens=1500,
        temperature=0.3,
        **llm_models.completion_kwargs(reasoning=False),
    )
    text = (resp.choices[0].message.content or "").strip()
    if "</think>" in text:
        text = text.split("</think>")[-1].strip()
    if not text:
        raise HTTPException(status_code=502, detail="Empty analysis from OpenRouter")
    return {
        "ok": True,
        "model": getattr(resp, "model", None) or llm_models.current_online_model(),
        "provider": "openrouter",
        "mode": mode,
        "analysis": text,
        "timing_ms": int((time.time() - start_time) * 1000),
    }


@router.post("/api/ollama/analyze-text")
def api_ollama_analyze_text(payload: OllamaTextAnalysisRequest):
    """Analyze text/code snippets using Ollama text model (no image required)."""
    text = payload.text
    mode = payload.mode
    language = payload.language

    TEXT_MODEL = _settings.ollama_vision_model
    logger.debug(f"[Ollama Text] Received text analysis request - length: {len(text)}, "
        f"mode: {mode}, model: {TEXT_MODEL}")

    try:
        import requests as req

        start_time = time.time()

        if mode == "code":
            prompt = f"""Analyze this {language} code briefly:

```{language}
{text}
```

Provide a concise analysis:
1. What it does
2. Key components
3. Any issues or suggestions

/no_think"""
        else:
            prompt = f"Analyze this text briefly:\n\n{text}\n\n/no_think"

        try:
            response = req.post(
                f"{_settings.ollama_base_url}/api/chat",
                json={
                    "model": TEXT_MODEL,
                    "stream": False,
                    "messages": [{"role": "user", "content": prompt}],
                    "options": {"num_predict": 500, "temperature": 0.3},
                },
                timeout=90,
            )
        except req.ConnectionError:
            response = None  # Ollama not running
        if (response is None or response.status_code == 404) and _settings.openrouter_api_key:
            # No local Ollama (or model not pulled): use the OpenRouter chat model.
            return _analyze_text_openrouter(prompt.replace("\n\n/no_think", ""), mode, start_time)
        if response is None:
            raise HTTPException(status_code=503, detail="Ollama is not running and no OpenRouter key is set.")

        timing_ms = int((time.time() - start_time) * 1000)

        if response.status_code != 200:
            error_text = response.text[:200] if response.text else "Unknown error"
            logger.error(f"[Ollama Text] Error response: {error_text}")
            raise HTTPException(
                status_code=500,
                detail=f"Ollama returned {response.status_code}: {error_text}",
            )

        result = response.json()
        message_obj = result.get("message", {})
        analysis_text = message_obj.get("content", "")

        if not analysis_text:
            thinking_text = message_obj.get("thinking", "")
            if thinking_text:
                analysis_text = thinking_text
                logger.info(f"[Ollama Text] Used thinking field as response ({len(analysis_text)} chars)")

        if analysis_text and "</think>" in analysis_text:
            parts = analysis_text.split("</think>")
            if len(parts) > 1:
                analysis_text = parts[-1].strip()

        if not analysis_text:
            logger.info(f"[Ollama Text] Empty response. Full result: {result}")
            raise HTTPException(status_code=500, detail="No analysis text returned")

        logger.debug(f"[Ollama Text] Analysis successful - {timing_ms}ms")

        return {
            "ok": True,
            "model": TEXT_MODEL,
            "mode": mode,
            "analysis": analysis_text,
            "timing_ms": timing_ms,
        }
    except HTTPException:
        raise
    except Exception as e:
        error_msg = str(e).encode("ascii", "replace").decode("ascii")
        logger.error(f"[Ollama Text] Exception: {error_msg}")
        traceback.print_exc()
        if "timed out" in str(e).lower() or "timeout" in str(e).lower():
            raise HTTPException(
                status_code=500,
                detail="Ollama timed out - the model may still be loading. Please try again in a moment.",
            )
        raise HTTPException(status_code=500, detail=str(e))
