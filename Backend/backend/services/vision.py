"""
Production Vision Service using Ollama + qwen3-vl:8b
Optimized for screenshots, UI images, and code snippets
"""

import asyncio
import aiohttp
import base64
import time
import io
import re
from typing import Optional, Dict, Any, Tuple
from PIL import Image
from dataclasses import dataclass, field

from backend.config import settings as _settings


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

    async def health_check(self) -> Dict[str, Any]:
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

                print(f"[VisionManager] Model check: looking for '{self.config.model}' in {models}, found={model_available}")

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
            health = await self.health_check()
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
        Analyze image using Ollama vision model

        Args:
            image_bytes: Raw image bytes
            mode: Analysis mode (general/ocr/ui/code)
            model_override: Override default model

        Returns:
            {ok, model, mode, analysis, timing_ms, preprocessing}
        """
        start = time.time()
        model = model_override or self.config.model

        try:
            # Validate mode
            if mode not in self.PROMPTS:
                return {
                    "ok": False,
                    "error": f"Invalid mode: {mode}. Must be one of: {list(self.PROMPTS.keys())}"
                }

            # Preprocess image
            processed_bytes, img_format = self._preprocess_image(image_bytes)

            # Encode to base64
            image_b64 = base64.b64encode(processed_bytes).decode('utf-8')

            # Get prompt
            prompt = self.PROMPTS[mode]

            # Build request
            # Note: qwen3 models may use thinking mode by default
            # Adding /no_think to prompt disables extended reasoning if needed
            payload = {
                "model": model,
                "stream": False,
                "messages": [
                    {
                        "role": "user",
                        "content": prompt + "\n\n/no_think",
                        "images": [image_b64]
                    }
                ],
                "options": {
                    "num_predict": self.config.max_tokens
                }
            }

            print(f"[VisionManager] Analyzing image: mode={mode}, size={len(processed_bytes)} bytes, format={img_format}")

            # Call Ollama
            session = await self._get_session()
            async with session.post(
                f"{self.config.ollama_url}/api/chat",
                json=payload
            ) as resp:
                if resp.status != 200:
                    error_text = await resp.text()
                    print(f"[VisionManager] Ollama error {resp.status}: {error_text[:300]}")
                    return {
                        "ok": False,
                        "error": f"Ollama API error {resp.status}: {error_text[:200]}"
                    }

                data = await resp.json()
                print(f"[VisionManager] Raw Ollama response keys: {list(data.keys())}")

                # Extract analysis text
                analysis_text = data.get("message", {}).get("content", "")

                # Debug: print full response if empty
                if not analysis_text:
                    print(f"[VisionManager] Empty content. Full response: {data}")
                    # Try alternative response formats
                    if "response" in data:
                        analysis_text = data["response"]
                    elif "content" in data:
                        analysis_text = data["content"]

                # Handle qwen3 thinking blocks - extract content after </think> if present
                if analysis_text and "</think>" in analysis_text:
                    parts = analysis_text.split("</think>")
                    if len(parts) > 1:
                        analysis_text = parts[-1].strip()
                        print(f"[VisionManager] Extracted content after thinking block")

                # Strip thinking block tags if they exist
                analysis_text = re.sub(r'<think>.*?</think>', '', analysis_text, flags=re.DOTALL).strip()

                if not analysis_text:
                    return {
                        "ok": False,
                        "error": f"Empty response from Ollama. Keys: {list(data.keys())}"
                    }

                elapsed_ms = int((time.time() - start) * 1000)

                print(f"[VisionManager] Analysis complete: {elapsed_ms}ms, {len(analysis_text)} chars")

                return {
                    "ok": True,
                    "model": model,
                    "mode": mode,
                    "analysis": analysis_text,
                    "timing_ms": elapsed_ms,
                    "preprocessing": {
                        "original_size": len(image_bytes),
                        "processed_size": len(processed_bytes),
                        "format": img_format
                    }
                }

        except asyncio.TimeoutError:
            elapsed_ms = int((time.time() - start) * 1000)
            return {
                "ok": False,
                "error": f"Request timeout after {elapsed_ms}ms"
            }
        except Exception as e:
            elapsed_ms = int((time.time() - start) * 1000)
            print(f"[VisionManager] Analysis error: {e}")
            return {
                "ok": False,
                "error": f"Analysis error: {str(e)}",
                "timing_ms": elapsed_ms
            }


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
