import os
from pathlib import Path
from typing import Any

import requests

from backend.config import settings


class LLMClient:
    """
    Unified LLM interface supporting:
      - ONLINE: OpenRouter (OpenAI-compatible) — model = openrouter/auto
      - LOCAL: Ollama via /api/generate (fallback to /api/chat if supported)

    SarahCore calls this with llm_mode="online" or "local".
    """

    def __init__(self, backend_root: Path):
        self.backend_root = Path(backend_root)

        self.online_model: str = settings.openrouter_model
        self.openrouter_api_key: str = settings.openrouter_api_key
        self.openrouter_base_url: str = settings.openrouter_base_url.rstrip("/")

        self.ollama_base_url: str = settings.ollama_base_url.rstrip("/")
        self.default_local_model: str = settings.default_local_model

        print(f"[LLM INIT] Online model: {self.online_model}")
        print(f"[LLM INIT] Default local model: {self.default_local_model}")
        print(f"[LLM INIT] OpenRouter API Key loaded: {'YES' if self.openrouter_api_key else 'NO'}")

    def generate(
        self,
        system_prompt: str,
        user_message: str,
        **kwargs: Any,
    ) -> str:
        """
        SarahCore always calls this function.

        kwargs may include:
            llm_mode="online" | "local"
            local_model="dolphin-mixtral"
        """
        llm_mode = (kwargs.get("llm_mode") or settings.llm_mode).lower()
        local_model = kwargs.get("local_model") or self.default_local_model

        print(f"[LLM] generate(mode={llm_mode}, local_model={local_model})")

        try:
            if llm_mode == "local":
                return self._call_ollama_generate(
                    system_prompt=system_prompt,
                    user_message=user_message,
                    model=local_model,
                )
            return self._call_openrouter_chat(system_prompt, user_message)

        except Exception as e:
            print(f"[LLM ERROR] Primary LLM failed (mode={llm_mode}): {e}")
            if llm_mode == "local":
                print("[LLM] Falling back to OpenRouter after Ollama failure.")
                try:
                    return self._call_openrouter_chat(system_prompt, user_message)
                except Exception as e2:
                    print(f"[LLM ERROR] OpenRouter fallback failed: {e2}")
                    return ""
            return ""

    def _call_openrouter_chat(self, system_prompt: str, user_message: str) -> str:
        if not self.openrouter_api_key:
            print("[LLM WARNING] SARAH_OPENROUTER_API_KEY not set; cannot call OpenRouter.")
            return ""

        url = f"{self.openrouter_base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.openrouter_api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.online_model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
            "temperature": settings.llm_temperature,
            "max_tokens": 1024,
        }

        print(f"[LLM] Calling OpenRouter model={self.online_model}")
        resp = requests.post(url, json=payload, headers=headers, timeout=60)
        resp.raise_for_status()
        data = resp.json()

        try:
            content = data["choices"][0]["message"]["content"]
        except Exception as e:
            print("[LLM ERROR] Unexpected OpenRouter response:", e, data)
            return ""

        return (content or "").strip()

    def _call_ollama_generate(
        self,
        system_prompt: str,
        user_message: str,
        model: str,
    ) -> str:
        prompt = f"{system_prompt}\nUser: {user_message}\nAssistant:"
        payload = {"model": model, "prompt": prompt, "stream": False}
        url = f"{self.ollama_base_url}/api/generate"

        print(f"[LLM OLLAMA] Calling Ollama model={model} at {url}")
        resp = requests.post(url, json=payload, timeout=300)

        try:
            resp.raise_for_status()
        except Exception as e:
            print("[LLM ERROR] Ollama returned HTTP error:", e)
            raise

        data = resp.json()
        return (data.get("response") or "").strip()

    def set_mode(self, llm_mode: str, local_model: str):
        print(f"[LLM] set_mode({llm_mode}, {local_model})")
        self._mode = llm_mode
        self._local_model = local_model
