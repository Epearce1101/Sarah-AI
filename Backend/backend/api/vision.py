"""Vision + screen-recording endpoints."""
from __future__ import annotations

import base64
import logging
from typing import Optional

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from backend.api.schemas import (
    ScreenRecordingStartResponse,
    ScreenRecordingStopResponse,
    ScreenshotAnalysisResponse,
    SmartAnalysisRequest,
    SmartAnalysisResponse,
    VideoSummaryRequest,
    VideoSummaryResponse,
    VisionRequest,
    VisionResponse,
)

SCREEN_ENABLED = False
SCREEN_IMPORT_ERROR: Optional[str] = None

try:
    from backend.screen.screen_capture import (
        get_last_recording,
        start_recording,
        stop_recording,
        summarize_recording,
    )
    from backend.screen.analyze_screenshot_bytes import analyze_screenshot_bytes
    from backend.screen.smart_analysis import (
        AnalysisMode,
        analyze_smart,
        format_code_extraction,
        format_error_analysis,
        format_ui_analysis,
    )
    SCREEN_ENABLED = True
except Exception as e:
    SCREEN_IMPORT_ERROR = str(e)
    start_recording = stop_recording = get_last_recording = None
    summarize_recording = analyze_screenshot_bytes = None
    analyze_smart = AnalysisMode = None
    format_error_analysis = format_ui_analysis = format_code_extraction = None

router = APIRouter()


@router.post("/vision/analyze")
async def vision_analyze(
    file: UploadFile = File(...),
    mode: str = Form("general"),
    model: Optional[str] = Form(None),
):
    """Analyze image using Ollama vision model (multipart upload)."""
    from backend.services.vision import VisionConfig, get_vision_manager

    allowed_types = ["image/png", "image/jpeg", "image/jpg", "image/webp"]
    if file.content_type not in allowed_types:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid file type: {file.content_type}. Allowed: {allowed_types}",
        )

    image_bytes = await file.read()
    max_size = VisionConfig().max_file_size_mb * 1024 * 1024
    if len(image_bytes) > max_size:
        raise HTTPException(
            status_code=400,
            detail=f"File too large: {len(image_bytes)} bytes. Max: {max_size} bytes",
        )

    vision = get_vision_manager()
    result = await vision.analyze(image_bytes=image_bytes, mode=mode, model_override=model)

    # Issue #16: return 200 with ok:false instead of 4xx/5xx so the renderer's
    # JSON error handling catches it without spamming "Failed to load resource"
    # in DevTools every time Ollama warms up or hiccups.
    return result


@router.post("/api/screen/start", response_model=ScreenRecordingStartResponse)
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


@router.post("/api/screen/stop", response_model=ScreenRecordingStopResponse)
def api_screen_stop():
    if not SCREEN_ENABLED or stop_recording is None:
        return ScreenRecordingStopResponse(
            ok=False, error=f"Screen capture is disabled: {SCREEN_IMPORT_ERROR}"
        )
    try:
        info = stop_recording()
        recording_path = info.get("path") or info.get("recording_path")
        return ScreenRecordingStopResponse(ok=True, recording_path=recording_path)
    except Exception as e:
        return ScreenRecordingStopResponse(ok=False, error=str(e))


@router.get("/api/screen/analyze_last", response_model=ScreenshotAnalysisResponse)
def api_screen_analyze_last():
    """Analyze the last recorded video and return a summary."""
    if not SCREEN_ENABLED or get_last_recording is None or summarize_recording is None:
        return ScreenshotAnalysisResponse(
            ok=False, error=f"Screen capture is disabled: {SCREEN_IMPORT_ERROR}"
        )
    try:
        last_recording = get_last_recording()
        if not last_recording or not last_recording.exists():
            return ScreenshotAnalysisResponse(ok=False, error="No recording found.")
        summary = summarize_recording(last_recording)
        return ScreenshotAnalysisResponse(
            ok=True,
            description=summary,
            suggestions="Recording analyzed successfully.",
        )
    except Exception as e:
        logging.exception("[/api/screen/analyze_last] Failed")
        return ScreenshotAnalysisResponse(ok=False, error=str(e))


@router.post("/api/vision", response_model=VisionResponse)
def api_vision(payload: VisionRequest):
    """Describe an image using SarahVision (OpenRouter/Ollama vision)."""
    if not SCREEN_ENABLED or analyze_screenshot_bytes is None:
        raise HTTPException(
            status_code=503, detail=f"Vision API is disabled: {SCREEN_IMPORT_ERROR}"
        )
    try:
        image_bytes = base64.b64decode(payload.image)
        analysis = analyze_screenshot_bytes(image_bytes)
        description = analysis.get("summary", "No description available.")
        raw_text = analysis.get("raw_text", "")
        if raw_text:
            description += f"\n\nVisible text: {raw_text}"
        return VisionResponse(description=description)
    except Exception as e:
        logging.exception("[/api/vision] Vision request failed")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/video-summary", response_model=VideoSummaryResponse)
def api_video_summary(payload: VideoSummaryRequest):
    """Summarize video frames using SarahVision (OpenRouter/Ollama vision)."""
    if not SCREEN_ENABLED or analyze_screenshot_bytes is None:
        raise HTTPException(
            status_code=503, detail=f"Vision API is disabled: {SCREEN_IMPORT_ERROR}"
        )
    try:
        frame_count = len(payload.frames)
        if frame_count == 0:
            return VideoSummaryResponse(summary="No frames provided for analysis.")

        frame_analyses = []
        for i, frame_b64 in enumerate(payload.frames):
            try:
                image_bytes = base64.b64decode(frame_b64)
                analysis = analyze_screenshot_bytes(image_bytes)
                frame_analyses.append({
                    "frame": i + 1,
                    "summary": analysis.get("summary", ""),
                    "raw_text": analysis.get("raw_text", ""),
                    "emotion": analysis.get("emotion", "neutral"),
                })
            except Exception as e:
                logging.warning(f"[/api/video-summary] Failed to analyze frame {i+1}: {e}")
                continue

        if not frame_analyses:
            return VideoSummaryResponse(summary="Failed to analyze video frames.")

        summary_parts = [f"Video recording analysis ({frame_count} frames):"]
        unique_summaries = []
        seen_summaries = set()
        for analysis in frame_analyses:
            summary_text = analysis["summary"]
            summary_key = summary_text[:50].lower()
            if summary_key not in seen_summaries:
                seen_summaries.add(summary_key)
                unique_summaries.append(analysis)

        for analysis in unique_summaries[:5]:
            summary_parts.append(f"• Frame {analysis['frame']}: {analysis['summary']}")
            if analysis["raw_text"]:
                summary_parts.append(f"  Text visible: {analysis['raw_text'][:100]}")

        if len(unique_summaries) > 5:
            summary_parts.append(f"• ... and {len(unique_summaries) - 5} more frames")

        summary = "\n".join(summary_parts)
        return VideoSummaryResponse(summary=summary)
    except Exception as e:
        logging.exception("[/api/video-summary] Video summary request failed")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/smart-analysis", response_model=SmartAnalysisResponse)
def api_smart_analysis(payload: SmartAnalysisRequest):
    """Smart screenshot analysis: error_detection / ui_analysis / code_recognition / general."""
    if not SCREEN_ENABLED or analyze_smart is None:
        raise HTTPException(
            status_code=503, detail=f"Smart Analysis is disabled: {SCREEN_IMPORT_ERROR}"
        )
    try:
        image_bytes = base64.b64decode(payload.image)
        try:
            mode = AnalysisMode(payload.mode)
        except ValueError:
            mode = AnalysisMode.GENERAL

        analysis = analyze_smart(image_bytes, mode=mode, auto_detect=payload.auto_detect)
        detected_mode = analysis.get("mode", "fallback")

        if detected_mode == AnalysisMode.ERROR_DETECTION:
            formatted = format_error_analysis(analysis)
        elif detected_mode == AnalysisMode.UI_ANALYSIS:
            formatted = format_ui_analysis(analysis)
        elif detected_mode == AnalysisMode.CODE_RECOGNITION:
            formatted = format_code_extraction(analysis)
        else:
            formatted = f"Analysis: {analysis.get('summary', 'No summary available')}"

        return SmartAnalysisResponse(
            mode=str(detected_mode), analysis=analysis, formatted=formatted
        )
    except Exception as e:
        logging.exception("[/api/smart-analysis] Smart analysis request failed")
        raise HTTPException(status_code=500, detail=str(e))
