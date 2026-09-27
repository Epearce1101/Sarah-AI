"""
Ollama Vision Integration for Local Image/Snippet Analysis
Model: qwen3-vl:8b (local, private, no external API required)
"""

import os
import base64
import requests
from typing import Dict, Any, Optional
import json
import time
from pathlib import Path

from backend.config import settings as _settings
import logging

logger = logging.getLogger(__name__)


class OllamaVision:
    """
    Ollama API client for local vision tasks using qwen3-vl:8b model.
    Keeps everything private and local on Windows.
    """

    # Vision analysis prompt templates per mode
    PROMPTS = {
        "general": (
            "Describe this image clearly and comprehensively. "
            "Include:\n"
            "1. Main subject/content\n"
            "2. Visual elements (colors, layout, composition)\n"
            "3. Any visible text (extract it verbatim)\n"
            "4. Context and purpose\n"
            "Be detailed but concise."
        ),
        "ocr": (
            "Extract ALL visible text from this image EXACTLY as shown.\n"
            "Requirements:\n"
            "- Preserve exact spelling, capitalization, and punctuation\n"
            "- Maintain line breaks and formatting\n"
            "- Include ALL text: titles, labels, buttons, errors, code, etc.\n"
            "- Do NOT add explanations\n"
            "- Output ONLY the extracted text"
        ),
        "ui": (
            "Analyze this UI/interface screenshot:\n"
            "1. Identify the application or interface type\n"
            "2. List main UI elements (buttons, menus, panels, inputs)\n"
            "3. If there are errors or warnings visible, explain them\n"
            "4. Describe the current state/context\n"
            "5. Suggest next steps or troubleshooting if errors present"
        ),
        "code": (
            "Extract and analyze the code visible in this image:\n"
            "1. First, output the complete code in ONE fenced code block (use ``` with language)\n"
            "2. Then explain:\n"
            "   - What the code does\n"
            "   - Programming language/framework\n"
            "   - Any visible errors or issues\n"
            "   - Debugging suggestions if needed\n"
            "Keep analysis practical and actionable."
        ),
    }

    def __init__(
        self,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
        timeout: int = 120
    ):
        base_url = base_url or _settings.ollama_base_url
        model = model or _settings.ollama_vision_model
        """
        Initialize Ollama Vision client.

        Args:
            base_url: Ollama API base URL (default: http://localhost:11434)
            model: Model name (default: qwen3-vl:8b)
            timeout: Request timeout in seconds (default: 120)
        """
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout
        self.chat_endpoint = f"{self.base_url}/api/chat"
        self.tags_endpoint = f"{self.base_url}/api/tags"

        logger.info(f"[OllamaVision] Initialized")
        logger.info(f"[OllamaVision] Base URL: {self.base_url}")
        logger.info(f"[OllamaVision] Model: {self.model}")
        logger.warning(f"[OllamaVision] Timeout: {self.timeout}s")

    def check_connection(self) -> Dict[str, Any]:
        """
        Check if Ollama server is reachable and model is available.

        Returns:
            Dict with 'ok', 'available' (bool), 'models' (list), 'error' (optional)
        """
        try:
            logger.info(f"[OllamaVision] Checking connection to {self.base_url}...")
            response = requests.get(self.tags_endpoint, timeout=5)

            if response.status_code != 200:
                return {
                    "ok": False,
                    "available": False,
                    "error": f"Ollama API returned status {response.status_code}"
                }

            data = response.json()
            models = [m["name"] for m in data.get("models", [])]

            model_available = self.model in models
            if not model_available:
                logger.warning(f"[OllamaVision] WARNING: Model '{self.model}' not found in available models: {models}")

            logger.info(f"[OllamaVision] Connection OK. Available models: {len(models)}")
            logger.info(f"[OllamaVision] Target model '{self.model}' available: {model_available}")

            return {
                "ok": True,
                "available": model_available,
                "models": models
            }

        except requests.ConnectionError:
            error_msg = f"Cannot connect to Ollama at {self.base_url}. Is Ollama running?"
            logger.error(f"[OllamaVision] {error_msg}")
            return {
                "ok": False,
                "available": False,
                "error": error_msg
            }
        except Exception as e:
            error_msg = f"Connection check failed: {str(e)}"
            logger.error(f"[OllamaVision] {error_msg}")
            return {
                "ok": False,
                "available": False,
                "error": error_msg
            }

    def analyze_image(
        self,
        image_data: bytes,
        mode: str = "general",
        custom_prompt: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Analyze an image using Ollama vision model.

        Args:
            image_data: Raw image bytes (PNG, JPEG, WebP)
            mode: Analysis mode - "general", "ocr", "ui", or "code"
            custom_prompt: Optional custom prompt (overrides mode prompt)

        Returns:
            Dict with:
                - ok: bool
                - model: str
                - mode: str
                - analysis: str (the response text)
                - timing_ms: int
                - error: str (optional, if failed)
        """
        start_time = time.time()
        request_id = int(start_time * 1000) % 1000000  # Simple request ID

        logger.info(f"[OllamaVision #{request_id}] Starting analysis")
        logger.info(f"[OllamaVision #{request_id}] Mode: {mode}")
        logger.info(f"[OllamaVision #{request_id}] Image size: {len(image_data)} bytes ({len(image_data) / 1024:.1f} KB)")

        # Validate mode
        if mode not in self.PROMPTS and custom_prompt is None:
            return {
                "ok": False,
                "model": self.model,
                "mode": mode,
                "analysis": "",
                "timing_ms": 0,
                "error": f"Invalid mode '{mode}'. Use: general, ocr, ui, or code"
            }

        # Get prompt
        prompt = custom_prompt if custom_prompt else self.PROMPTS[mode]
        logger.info(f"[OllamaVision #{request_id}] Prompt: {prompt[:100]}...")

        # Encode image to base64
        try:
            image_b64 = base64.b64encode(image_data).decode('utf-8')
            # Don't print base64 (security/privacy)
            logger.info(f"[OllamaVision #{request_id}] Base64 encoding: {len(image_b64)} chars")
        except Exception as e:
            return {
                "ok": False,
                "model": self.model,
                "mode": mode,
                "analysis": "",
                "timing_ms": int((time.time() - start_time) * 1000),
                "error": f"Image encoding failed: {str(e)}"
            }

        # Build Ollama API request
        payload = {
            "model": self.model,
            "stream": False,
            "messages": [
                {
                    "role": "user",
                    "content": prompt,
                    "images": [image_b64]
                }
            ]
        }

        # Call Ollama API
        try:
            logger.info(f"[OllamaVision #{request_id}] Calling Ollama API...")
            response = requests.post(
                self.chat_endpoint,
                json=payload,
                timeout=self.timeout
            )

            timing_ms = int((time.time() - start_time) * 1000)

            if response.status_code != 200:
                error_detail = response.text[:500]
                logger.error(f"[OllamaVision #{request_id}] API error {response.status_code}: {error_detail}")
                return {
                    "ok": False,
                    "model": self.model,
                    "mode": mode,
                    "analysis": "",
                    "timing_ms": timing_ms,
                    "error": f"Ollama API error {response.status_code}: {error_detail}"
                }

            result = response.json()
            logger.info(f"[OllamaVision #{request_id}] Response received")

            # Extract response text
            if "message" in result and "content" in result["message"]:
                analysis_text = result["message"]["content"]
                logger.info(f"[OllamaVision #{request_id}] Analysis complete: {len(analysis_text)} chars in {timing_ms}ms")

                return {
                    "ok": True,
                    "model": self.model,
                    "mode": mode,
                    "analysis": analysis_text,
                    "timing_ms": timing_ms
                }
            else:
                logger.info(f"[OllamaVision #{request_id}] Unexpected response format")
                return {
                    "ok": False,
                    "model": self.model,
                    "mode": mode,
                    "analysis": "",
                    "timing_ms": timing_ms,
                    "error": "Unexpected response format from Ollama"
                }

        except requests.Timeout:
            timing_ms = int((time.time() - start_time) * 1000)
            logger.warning(f"[OllamaVision #{request_id}] Request timed out after {timing_ms}ms")
            return {
                "ok": False,
                "model": self.model,
                "mode": mode,
                "analysis": "",
                "timing_ms": timing_ms,
                "error": f"Request timed out after {self.timeout}s"
            }
        except requests.ConnectionError:
            timing_ms = int((time.time() - start_time) * 1000)
            logger.error(f"[OllamaVision #{request_id}] Connection error")
            return {
                "ok": False,
                "model": self.model,
                "mode": mode,
                "analysis": "",
                "timing_ms": timing_ms,
                "error": f"Cannot connect to Ollama at {self.base_url}. Is Ollama running?"
            }
        except Exception as e:
            timing_ms = int((time.time() - start_time) * 1000)
            logger.error(f"[OllamaVision #{request_id}] Exception: {type(e).__name__}: {str(e)}")
            import traceback
            traceback.print_exc()
            return {
                "ok": False,
                "model": self.model,
                "mode": mode,
                "analysis": "",
                "timing_ms": timing_ms,
                "error": f"Analysis failed: {str(e)}"
            }

    def warmup(self) -> bool:
        """
        Warm up the model by making a simple request.
        Call this on server startup to load the model into memory.

        Returns:
            True if warmup successful, False otherwise
        """
        logger.info("[OllamaVision] Starting model warmup...")

        # Create a tiny 1x1 red PNG image
        tiny_png = base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8DwHwAFBQIAX8jx0gAAAABJRU5ErkJggg=="
        )

        result = self.analyze_image(
            image_data=tiny_png,
            mode="general"
        )

        if result["ok"]:
            logger.info(f"[OllamaVision] Warmup successful ({result['timing_ms']}ms)")
            return True
        else:
            logger.error(f"[OllamaVision] Warmup failed: {result.get('error')}")
            return False


# Singleton instance
_ollama_vision: Optional[OllamaVision] = None


def get_ollama_vision(
    base_url: Optional[str] = None,
    model: Optional[str] = None,
) -> OllamaVision:
    """Get or create singleton Ollama Vision instance."""
    global _ollama_vision
    if _ollama_vision is None:
        _ollama_vision = OllamaVision(
            base_url=base_url or _settings.ollama_base_url,
            model=model or _settings.ollama_vision_model,
        )
        logger.info(f"[OllamaVision] Singleton created")
    return _ollama_vision
