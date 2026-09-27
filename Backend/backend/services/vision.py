"""
Production Vision Service using Ollama + qwen3-vl:8b
Optimized for screenshots, UI images, and code snippets
"""

import asyncio
import aiohttp
import base64
import logging
import time
import io
import re
from typing import Optional, Dict, Any, Tuple
from PIL import Image
from dataclasses import dataclass, field

from backend import llm_models
from backend.config import settings as _settings

logger = logging.getLogger("sarah.vision")
OPENROUTER_VISION_TIMEOUT = 120


@dataclass
class VisionConfig:
    """Configuration for Ollama vision service"""
    ollama_url: str = field(default_factory=lambda: _settings.ollama_base_url)
    model: str = field(default_factory=lambda: _settings.ollama_vision_model)
    timeout: int = 120  # seconds
    max_tokens: int = 1024  # num_predict - increased for qwen3 thinking mode
    max_image_width: int = 1600  # resize large images
    max_image_height: int = 1600
    max_file_size_mb: int = 12
    warmup_enabled: bool = True


class VisionManager:
    """Manages Ollama vision model with health checks and optimization"""

    # Mode-specific prompts optimized for concise responses
    PROMPTS = {
        "general": """Describe this image clearly and concisely. Include:
1. Main subject/content
2. Any visible text (quote exactly)
3. Key visual elements
Keep response under 200 words.""",

        "ocr": """Extract ALL visible text from this image EXACTLY as shown.
Preserve line breaks and formatting.
Output ONLY the text, nothing else.""",

        "ui": """Analyze this UI/application screenshot:
1. What screen/application is this?
2. List key UI elements visible
3. If there are errors/issues, identify them
4. Suggest fix steps if applicable
Keep response under 250 words.""",

        "code": """Extract the code from this image into a single fenced code block.
Then briefly explain what it does and identify any bugs/issues.
Format:
```language
<exact code here>
```
<brief explanation>"""
    }

    def __init__(self, config: Optional[VisionConfig] = None):
        self.config = config or VisionConfig()
        self._last_health_check: Optional[Dict[str, Any]] = None
        self._last_warmup_ms: Optional[int] = None
        self._last_error: Optional[str] = None
        self._warmup_done: bool = False
        self._session: Optional[aiohttp.ClientSession] = None
        print(f"[VisionManager] Initialized with model={self.config.model}, url={self.config.ollama_url}")

    async def _get_session(self) -> aiohttp.ClientSession:
        """Get or create aiohttp session"""
        if self._session is None or self._session.closed:
            timeout = aiohttp.ClientTimeout(total=self.config.timeout)
            self._session = aiohttp.ClientSession(timeout=timeout)
        return self._session

    async def close(self):
        """Close aiohttp session"""
        if self._session and not self._session.closed:
            await self._session.close()

    @staticmethod
    def _cloud_available() -> bool:
        return bool(_settings.openrouter_api_key)

    async def health_check(self) -> Dict[str, Any]:
        """Vision readiness: local Ollama if usable, else the OpenRouter model.

        Returns {ok, ollama_reachable, vision_ready, provider, model, ...}.
        `provider` is "ollama" or "openrouter"; with neither, vision_ready is
        False and the Ollama error explains why.
        """
        local = await self._ollama_health()
        if local.get("vision_ready"):
            return {**local, "provider": "ollama"}
        if self._cloud_available():
            return {
                **local,
                "ok": True,
                "vision_ready": True,
                "provider": "openrouter",
                "model": llm_models.current_vision_model(),
                "local_model": self.config.model,
                "fallback_reason": local.get("error") or local.get("last_error") or "local model not installed",
            }
        return {**local, "provider": None}

    async def _ollama_health(self) -> Dict[str, Any]:
        """
        Check if Ollama is reachable and model is available
        Returns: {ok, ollama_reachable, vision_ready, model, models_available}
        """
        try:
            session = await self._get_session()

            # Check Ollama base endpoint
            async with session.get(
                f"{self.config.ollama_url}/api/tags",
                timeout=aiohttp.ClientTimeout(total=5)
            ) as resp:
                if resp.status != 200:
                    self._last_error = f"Ollama returned status {resp.status}"
                    return {
                        "ok": False,
                        "ollama_reachable": False,
                        "vision_ready": False,
                        "model": self.config.model,
                        "error": self._last_error
                    }

                data = await resp.json()
                models = [m["name"] for m in data.get("models", [])]

                # Flexible model matching - check if our model name is contained in any available model
                # This handles cases like "qwen3-vl:8b" matching "qwen3-vl:8b" or "qwen3-vl:latest"
                model_base = self.config.model.split(":")[0]  # e.g., "qwen3-vl"
                model_available = any(
                    self.config.model == m or  # Exact match
                    model_base in m  # Base name match
                    for m in models
                )

                logger.debug("Model check: looking for %r in %s, found=%s", self.config.model, models, model_available)

                self._last_error = None if model_available else f"Model {self.config.model} not found in {models}"

                # Vision is ready as long as Ollama is reachable and model is available
                # No warmup required - greenlighting immediately when model found
                result = {
                    "ok": True,
                    "ollama_reachable": True,
                    "vision_ready": model_available,  # Ready when model available (no warmup needed)
                    "model": self.config.model,
                    "models_available": models,
                    "warmup_done": True,  # Skip warmup requirement
                    "last_warmup_ms": self._last_warmup_ms,
                    "last_error": self._last_error
                }

                self._last_health_check = result
                return result

        except asyncio.TimeoutError:
            self._last_error = "Ollama connection timeout"
            return {
                "ok": False,
                "ollama_reachable": False,
                "vision_ready": False,
                "model": self.config.model,
                "error": self._last_error
            }
        except Exception as e:
            self._last_error = f"Health check error: {str(e)}"
            return {
                "ok": False,
                "ollama_reachable": False,
                "vision_ready": False,
                "model": self.config.model,
                "error": self._last_error
            }

    async def warm_up(self) -> bool:
        """
        Warm up the vision model with a tiny request (no image)
        Returns: True if successful, False otherwise
        Non-blocking - called via asyncio.create_task on startup
        """
        if not self.config.warmup_enabled:
            print("[VisionManager] Warmup disabled")
            return False

        print(f"[VisionManager] Starting warmup for {self.config.model}...")
        start = time.time()

        try:
            # First check health
            health = await self._ollama_health()
            if not health.get("ollama_reachable") or not health.get("ok"):
                print(f"[VisionManager] Warmup skipped - Ollama not reachable")
                return False

            # Check if model is available
            if self.config.model not in health.get("models_available", []):
                print(f"[VisionManager] Warmup skipped - model {self.config.model} not found")
                self._last_error = f"Model {self.config.model} not installed"
                return False

            # Send tiny warmup request (text-only, no image)
            session = await self._get_session()
            payload = {
                "model": self.config.model,
                "stream": False,
                "messages": [
                    {"role": "user", "content": "Hello"}
                ],
                "options": {"num_predict": 5}
            }

            async with session.post(
                f"{self.config.ollama_url}/api/chat",
                json=payload,
                timeout=aiohttp.ClientTimeout(total=60)
            ) as resp:
                if resp.status != 200:
                    error_text = await resp.text()
                    print(f"[VisionManager] Warmup failed: HTTP {resp.status} - {error_text[:200]}")
                    self._last_error = f"Warmup failed: {resp.status}"
                    return False

                await resp.json()  # consume response

                elapsed_ms = int((time.time() - start) * 1000)
                self._last_warmup_ms = elapsed_ms
                self._warmup_done = True
                self._last_error = None

                print(f"[VisionManager] Warmup successful ({elapsed_ms}ms)")
                return True

        except asyncio.TimeoutError:
            print("[VisionManager] Warmup timeout")
            self._last_error = "Warmup timeout"
            return False
        except Exception as e:
            print(f"[VisionManager] Warmup error: {e}")
            self._last_error = f"Warmup error: {str(e)}"
            return False

    def _preprocess_image(self, image_bytes: bytes) -> Tuple[bytes, str]:
        """
        Preprocess image: resize if large, convert to JPEG if needed
        Returns: (processed_bytes, format)
        """
        try:
            img = Image.open(io.BytesIO(image_bytes))
            original_format = img.format or "PNG"

            # Get original size
            width, height = img.size

            # Resize if too large
            max_w = self.config.max_image_width
            max_h = self.config.max_image_height

            if width > max_w or height > max_h:
                # Calculate new size maintaining aspect ratio
                ratio = min(max_w / width, max_h / height)
                new_size = (int(width * ratio), int(height * ratio))
                img = img.resize(new_size, Image.Resampling.LANCZOS)
                print(f"[VisionManager] Resized image from {width}x{height} to {new_size[0]}x{new_size[1]}")

            # Convert to RGB if necessary (handles RGBA, grayscale, etc.)
            if img.mode not in ("RGB", "L"):
                if img.mode == "RGBA":
                    # Create white background for RGBA
                    background = Image.new("RGB", img.size, (255, 255, 255))
                    background.paste(img, mask=img.split()[3])  # use alpha channel as mask
                    img = background
                else:
                    img = img.convert("RGB")

            # Save to bytes
            output = io.BytesIO()
            img.save(output, format="JPEG", quality=90, optimize=True)
            processed_bytes = output.getvalue()

            print(f"[VisionManager] Image preprocessing: {len(image_bytes)} -> {len(processed_bytes)} bytes")

            return processed_bytes, "JPEG"

        except Exception as e:
            print(f"[VisionManager] Image preprocessing failed: {e}, using original")
            return image_bytes, "original"

    async def analyze(
        self,
        image_bytes: bytes,
        mode: str = "general",
        model_override: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Analyze an image: local Ollama when it's usable, else OpenRouter.

        Args:
            image_bytes: Raw image bytes
            mode: Analysis mode (general/ocr/ui/code)
            model_override: Force a specific *Ollama* model (no cloud fallback)

        Returns:
            {ok, model, mode, analysis, timing_ms, provider, preprocessing}
        """
        start = time.time()
        if mode not in self.PROMPTS:
            return {
                "ok": False,
                "error": f"Invalid mode: {mode}. Must be one of: {list(self.PROMPTS.keys())}"
            }

        processed_bytes, img_format = self._preprocess_image(image_bytes)
        prompt = self.PROMPTS[mode]

        local_error = None
        use_local = bool(model_override) or (await self._ollama_health()).get("vision_ready")
        if use_local:
            result = await self._analyze_ollama(processed_bytes, prompt, model_override or self.config.model)
            if result.get("ok") or model_override or not self._cloud_available():
                return self._finish(result, start, mode, image_bytes, processed_bytes, img_format)
            local_error = result.get("error")
            logger.warning("Ollama vision failed (%s); falling back to OpenRouter", local_error)

        if not self._cloud_available():
            return {
                "ok": False,
                "error": "No vision backend: Ollama isn't available and no OpenRouter API key is set.",
                "timing_ms": int((time.time() - start) * 1000),
            }

        result = await self._analyze_openrouter(processed_bytes, prompt)
        if local_error:
            result["local_error"] = local_error
        return self._finish(result, start, mode, image_bytes, processed_bytes, img_format)

    @staticmethod
    def _finish(result, start, mode, image_bytes, processed_bytes, img_format) -> Dict[str, Any]:
        result.setdefault("timing_ms", int((time.time() - start) * 1000))
        if result.get("ok"):
            result.update(
                mode=mode,
                preprocessing={
                    "original_size": len(image_bytes),
                    "processed_size": len(processed_bytes),
                    "format": img_format,
                },
            )
            logger.info("Vision analysis via %s: %sms, %d chars",
                        result.get("provider"), result["timing_ms"], len(result.get("analysis", "")))
        return result

    @staticmethod
    def _clean_analysis(text: str) -> str:
        # Reasoning models may emit a thinking block before the answer.
        if text and "</think>" in text:
            text = text.split("</think>")[-1]
        return re.sub(r"<think>.*?</think>", "", text or "", flags=re.DOTALL).strip()

    async def _analyze_ollama(self, processed_bytes: bytes, prompt: str, model: str) -> Dict[str, Any]:
        image_b64 = base64.b64encode(processed_bytes).decode("utf-8")
        # qwen3 models think by default; /no_think keeps answers short.
        payload = {
            "model": model,
            "stream": False,
            "messages": [{"role": "user", "content": prompt + "\n\n/no_think", "images": [image_b64]}],
            "options": {"num_predict": self.config.max_tokens},
        }
        try:
            session = await self._get_session()
            async with session.post(f"{self.config.ollama_url}/api/chat", json=payload) as resp:
                if resp.status != 200:
                    error_text = await resp.text()
                    return {"ok": False, "error": f"Ollama API error {resp.status}: {error_text[:200]}"}
                data = await resp.json()
        except asyncio.TimeoutError:
            return {"ok": False, "error": "Ollama request timed out"}
        except Exception as e:
            return {"ok": False, "error": f"Ollama error: {e}"}

        text = (data.get("message") or {}).get("content") or data.get("response") or data.get("content") or ""
        text = self._clean_analysis(text)
        if not text:
            return {"ok": False, "error": f"Empty response from Ollama. Keys: {list(data.keys())}"}
        return {"ok": True, "provider": "ollama", "model": model, "analysis": text}

    async def _analyze_openrouter(self, processed_bytes: bytes, prompt: str) -> Dict[str, Any]:
        image_b64 = base64.b64encode(processed_bytes).decode("utf-8")
        body = llm_models.vision_request_body(
            [{
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"}},
                ],
            }],
            max_tokens=self.config.max_tokens,
        )
        url = f"{_settings.openrouter_base_url.rstrip('/')}/chat/completions"
        headers = {"Authorization": f"Bearer {_settings.openrouter_api_key}", "Content-Type": "application/json"}
        try:
            session = await self._get_session()
            async with session.post(url, json=body, headers=headers,
                                    timeout=aiohttp.ClientTimeout(total=OPENROUTER_VISION_TIMEOUT)) as resp:
                data = await resp.json(content_type=None)
                if resp.status != 200:
                    message = (data.get("error") or {}).get("message") if isinstance(data, dict) else None
                    return {"ok": False, "error": f"OpenRouter vision error {resp.status}: {message or str(data)[:200]}"}
        except asyncio.TimeoutError:
            return {"ok": False, "error": "OpenRouter vision request timed out"}
        except Exception as e:
            return {"ok": False, "error": f"OpenRouter vision error: {e}"}

        choices = data.get("choices") or []
        text = self._clean_analysis(((choices[0] if choices else {}).get("message") or {}).get("content") or "")
        if not text:
            return {"ok": False, "error": "Empty response from OpenRouter vision model"}
        return {"ok": True, "provider": "openrouter", "model": data.get("model") or body["model"], "analysis": text}


# Global singleton
_vision_manager: Optional[VisionManager] = None


def get_vision_manager(config: Optional[VisionConfig] = None) -> VisionManager:
    """Get or create global VisionManager instance"""
    global _vision_manager
    if _vision_manager is None:
        _vision_manager = VisionManager(config)
    return _vision_manager


async def cleanup_vision_manager():
    """Cleanup global VisionManager (call on shutdown)"""
    global _vision_manager
    if _vision_manager:
        await _vision_manager.close()
        _vision_manager = None
