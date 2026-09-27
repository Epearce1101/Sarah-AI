"""
Smart Screenshot Analysis - Specialized Analysis Modes
======================================================

This module provides specialized screenshot analysis capabilities:
- Error detection: Recognizes stack traces and highlights actual error lines
- UI analysis: Detects misalignment, spacing issues, and visual bugs
- Code recognition: Accurately extracts code from screenshots with syntax awareness
"""

import base64
import os
import logging
import json
import re
from typing import Optional, Dict, Any, List, Tuple
from enum import Enum

import requests

from backend.config import settings as _settings

logger = logging.getLogger("sarah.smart_analysis")


class AnalysisMode(str, Enum):
    """Specialized analysis modes for screenshots."""

    ERROR_DETECTION = "error_detection"
    UI_ANALYSIS = "ui_analysis"
    CODE_RECOGNITION = "code_recognition"
    GENERAL = "general"


# ---------------------------------------------------------
# ERROR DETECTION MODE
# ---------------------------------------------------------

def _call_llm_error_detection(image_bytes: bytes) -> Optional[Dict[str, Any]]:
    """
    Specialized error detection mode.

    Focuses on:
    - Identifying stack traces and error messages
    - Highlighting the actual error line (not just the first line)
    - Extracting file paths, line numbers, and error types
    - Providing actionable fix suggestions
    """
    api_key = _settings.openrouter_api_key or os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        return None

    model = _settings.openrouter_model
    image_b64 = base64.b64encode(image_bytes).decode("ascii")
    url = f"{_settings.openrouter_base_url}/chat/completions"

    system_prompt = """You are SarahVision Error Detective, an expert at analyzing error screenshots.

Your task: Identify and analyze errors, stack traces, and exceptions in screenshots.

Respond with STRICT JSON ONLY (no markdown, no comments):
{
  "has_error": boolean,
  "error_type": string,           // e.g., "TypeError", "SyntaxError", "RuntimeError", "HTTP 500", etc.
  "error_message": string,        // The actual error message
  "error_line": string,           // The ACTUAL line causing the error (not the first line of stack trace)
  "file_path": string,            // File path if visible
  "line_number": number,          // Line number if visible
  "stack_trace": string[],        // Array of stack trace lines (up to 10 most relevant)
  "root_cause": string,           // Your analysis of the root cause
  "suggested_fix": string,        // Actionable suggestion to fix the error
  "severity": string,             // "critical", "error", "warning", "info"
  "confidence": number            // 0.0 to 1.0 - how confident you are this is an error
}

CRITICAL: Focus on the ACTUAL error line, not just the top of the stack trace.
Look for indicators like "^^^", highlighted lines, or the deepest user code in the stack."""

    user_text = """Analyze this screenshot for errors, exceptions, or stack traces.
If you see an error:
1. Find the ACTUAL line causing the error (often marked with ^^^ or highlighted)
2. Extract the complete error message
3. Identify the root cause
4. Suggest a specific fix

If no error is visible, set has_error to false."""

    payload = {
        "model": model,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": user_text},
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"}
                    },
                ],
            },
        ],
        "max_tokens": 1024,
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

        content = choices[0].get("message", {}).get("content", "")
        if isinstance(content, str):
            return json.loads(content.strip())

        return None
    except Exception as exc:
        logger.warning("LLM error detection failed: %s", exc)
        return None


# ---------------------------------------------------------
# UI ANALYSIS MODE
# ---------------------------------------------------------

def _call_llm_ui_analysis(image_bytes: bytes) -> Optional[Dict[str, Any]]:
    """
    Specialized UI analysis mode.

    Focuses on:
    - Detecting visual alignment issues
    - Identifying spacing/padding problems
    - Spotting color contrast issues
    - Finding responsive design problems
    - Checking accessibility concerns
    """
    api_key = _settings.openrouter_api_key or os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        return None

    model = _settings.openrouter_model
    image_b64 = base64.b64encode(image_bytes).decode("ascii")
    url = f"{_settings.openrouter_base_url}/chat/completions"

    system_prompt = """You are SarahVision UI Inspector, a design-focused visual analyzer.

Your task: Inspect UI screenshots for design issues and improvement opportunities.

Respond with STRICT JSON ONLY (no markdown, no comments):
{
  "overall_quality": string,      // "excellent", "good", "needs_improvement", "poor"
  "issues": [                     // Array of detected issues
    {
      "type": string,              // "alignment", "spacing", "contrast", "typography", "responsive", "accessibility"
      "severity": string,          // "critical", "major", "minor", "suggestion"
      "description": string,       // Specific description: "This button is misaligned by 3px to the right"
      "location": string,          // Where in the UI: "top-right corner", "login form", "navigation bar"
      "suggestion": string         // How to fix: "Add left-margin: -3px" or "Increase padding to 16px"
    }
  ],
  "positive_aspects": string[],   // Things that look good
  "color_scheme": string,         // Brief description of color palette
  "accessibility_score": number,  // 0-10 estimated accessibility score
  "mobile_ready": boolean,        // Whether it looks mobile-friendly
  "summary": string               // One sentence overall assessment
}

Focus on actionable, specific feedback. Instead of "button looks off", say "button is misaligned 5px left"."""

    user_text = """Analyze this UI screenshot for design and usability issues.
Look for:
- Alignment problems (elements not lined up properly)
- Spacing inconsistencies (uneven padding/margins)
- Color contrast issues (text hard to read)
- Typography problems (font sizes, line heights)
- Responsive design concerns
- Accessibility issues

Be specific and actionable in your feedback."""

    payload = {
        "model": model,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": user_text},
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"}
                    },
                ],
            },
        ],
        "max_tokens": 1024,
        "temperature": 0.2,
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

        content = choices[0].get("message", {}).get("content", "")
        if isinstance(content, str):
            return json.loads(content.strip())

        return None
    except Exception as exc:
        logger.warning("LLM UI analysis failed: %s", exc)
        return None


# ---------------------------------------------------------
# CODE RECOGNITION MODE
# ---------------------------------------------------------

def _call_llm_code_extraction(image_bytes: bytes) -> Optional[Dict[str, Any]]:
    """
    Specialized code extraction mode.

    Focuses on:
    - Accurately extracting code from screenshots
    - Detecting programming language
    - Preserving syntax and indentation
    - Identifying code context (function name, class, file)
    """
    api_key = _settings.openrouter_api_key or os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        return None

    model = _settings.openrouter_model
    image_b64 = base64.b64encode(image_bytes).decode("ascii")
    url = f"{_settings.openrouter_base_url}/chat/completions"

    system_prompt = """You are SarahVision Code Extractor, an expert at reading code from screenshots.

Your task: Extract code from screenshots with perfect accuracy.

Respond with STRICT JSON ONLY (no markdown, no comments):
{
  "has_code": boolean,
  "language": string,             // "python", "javascript", "typescript", "rust", "go", "java", etc.
  "code": string,                 // The extracted code with EXACT indentation preserved
  "code_type": string,            // "function", "class", "snippet", "config", "terminal_output"
  "context": {
    "file_name": string,          // Visible filename or null
    "function_name": string,      // Function/class name if visible
    "line_numbers": [number, number],  // [start, end] if visible
    "editor": string              // "vscode", "sublime", "vim", "terminal", etc. if detectable
  },
  "syntax_highlighted": boolean,  // Whether the code has syntax highlighting
  "confidence": number,           // 0.0-1.0 how confident the extraction is
  "notes": string                 // Any important observations about the code
}

CRITICAL: Preserve EXACT indentation. Code MUST be runnable."""

    user_text = """Extract code from this screenshot with perfect accuracy.

Requirements:
1. Preserve exact indentation (spaces/tabs)
2. Include all visible lines
3. Detect the programming language
4. Extract any visible context (filename, line numbers, function names)
5. If multiple code blocks are visible, extract the most prominent one

If no code is visible, set has_code to false."""

    payload = {
        "model": model,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": user_text},
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"}
                    },
                ],
            },
        ],
        "max_tokens": 2048,
        "temperature": 0.0,  # Maximum precision for code extraction
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

        content = choices[0].get("message", {}).get("content", "")
        if isinstance(content, str):
            return json.loads(content.strip())

        return None
    except Exception as exc:
        logger.warning("LLM code extraction failed: %s", exc)
        return None


# ---------------------------------------------------------
# SMART ANALYSIS ORCHESTRATOR
# ---------------------------------------------------------

def analyze_smart(
    image_bytes: bytes,
    mode: AnalysisMode = AnalysisMode.GENERAL,
    auto_detect: bool = True
) -> Dict[str, Any]:
    """
    Smart screenshot analysis with specialized modes.

    Args:
        image_bytes: The screenshot image data
        mode: Specific analysis mode to use
        auto_detect: If True and mode=GENERAL, automatically detect best mode

    Returns:
        Structured analysis result based on the mode
    """

    # Auto-detection logic (if mode is GENERAL and auto_detect is True)
    if mode == AnalysisMode.GENERAL and auto_detect:
        # Quick heuristic: try error detection first (fastest to fail)
        error_result = _call_llm_error_detection(image_bytes)
        if error_result and error_result.get("has_error") and error_result.get("confidence", 0) > 0.7:
            return {
                "mode": AnalysisMode.ERROR_DETECTION,
                "auto_detected": True,
                **error_result
            }

        # Try code extraction
        code_result = _call_llm_code_extraction(image_bytes)
        if code_result and code_result.get("has_code") and code_result.get("confidence", 0) > 0.7:
            return {
                "mode": AnalysisMode.CODE_RECOGNITION,
                "auto_detected": True,
                **code_result
            }

        # Default to UI analysis for everything else
        mode = AnalysisMode.UI_ANALYSIS

    # Execute specific mode
    if mode == AnalysisMode.ERROR_DETECTION:
        result = _call_llm_error_detection(image_bytes)
        if result:
            result["mode"] = AnalysisMode.ERROR_DETECTION
            return result

    elif mode == AnalysisMode.CODE_RECOGNITION:
        result = _call_llm_code_extraction(image_bytes)
        if result:
            result["mode"] = AnalysisMode.CODE_RECOGNITION
            return result

    elif mode == AnalysisMode.UI_ANALYSIS:
        result = _call_llm_ui_analysis(image_bytes)
        if result:
            result["mode"] = AnalysisMode.UI_ANALYSIS
            return result

    # Fallback to basic analysis
    from .analyze_screenshot_bytes import analyze_screenshot_bytes
    basic_result = analyze_screenshot_bytes(image_bytes)
    basic_result["mode"] = "fallback"
    return basic_result


def format_error_analysis(analysis: Dict[str, Any]) -> str:
    """Format error analysis result into human-readable text."""
    if not analysis.get("has_error"):
        return "No errors detected in screenshot."

    lines = ["🔴 ERROR DETECTED\n"]

    error_type = analysis.get("error_type", "Unknown Error")
    error_msg = analysis.get("error_message", "")
    lines.append(f"Type: {error_type}")

    if error_msg:
        lines.append(f"Message: {error_msg}\n")

    error_line = analysis.get("error_line")
    if error_line:
        lines.append(f"Error Line: {error_line}")

    file_path = analysis.get("file_path")
    line_num = analysis.get("line_number")
    if file_path:
        location = f"{file_path}"
        if line_num:
            location += f":{line_num}"
        lines.append(f"Location: {location}\n")

    root_cause = analysis.get("root_cause")
    if root_cause:
        lines.append(f"Root Cause: {root_cause}\n")

    suggested_fix = analysis.get("suggested_fix")
    if suggested_fix:
        lines.append(f"💡 Suggested Fix:\n{suggested_fix}")

    return "\n".join(lines)


def format_ui_analysis(analysis: Dict[str, Any]) -> str:
    """Format UI analysis result into human-readable text."""
    lines = ["🎨 UI ANALYSIS\n"]

    quality = analysis.get("overall_quality", "unknown")
    lines.append(f"Overall Quality: {quality.upper()}\n")

    issues = analysis.get("issues", [])
    if issues:
        lines.append(f"Issues Found ({len(issues)}):")
        for i, issue in enumerate(issues, 1):
            severity = issue.get("severity", "unknown")
            desc = issue.get("description", "")
            location = issue.get("location", "")
            suggestion = issue.get("suggestion", "")

            severity_emoji = {
                "critical": "🔴",
                "major": "🟡",
                "minor": "🟢",
                "suggestion": "💡"
            }.get(severity, "•")

            lines.append(f"\n{severity_emoji} Issue {i}: {desc}")
            if location:
                lines.append(f"   Location: {location}")
            if suggestion:
                lines.append(f"   Fix: {suggestion}")

    positives = analysis.get("positive_aspects", [])
    if positives:
        lines.append(f"\n✅ Positive Aspects:")
        for pos in positives:
            lines.append(f"  • {pos}")

    summary = analysis.get("summary")
    if summary:
        lines.append(f"\nSummary: {summary}")

    return "\n".join(lines)


def format_code_extraction(analysis: Dict[str, Any]) -> str:
    """Format code extraction result into human-readable text."""
    if not analysis.get("has_code"):
        return "No code detected in screenshot."

    lines = ["💻 CODE EXTRACTION\n"]

    language = analysis.get("language", "unknown")
    lines.append(f"Language: {language}")

    context = analysis.get("context", {})
    if context.get("file_name"):
        lines.append(f"File: {context['file_name']}")
    if context.get("function_name"):
        lines.append(f"Function: {context['function_name']}")

    code = analysis.get("code", "")
    if code:
        lines.append(f"\n```{language}")
        lines.append(code)
        lines.append("```")

    confidence = analysis.get("confidence", 0)
    lines.append(f"\nConfidence: {confidence * 100:.1f}%")

    notes = analysis.get("notes")
    if notes:
        lines.append(f"Notes: {notes}")

    return "\n".join(lines)
