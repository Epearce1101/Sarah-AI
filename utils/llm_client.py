import os
import json
from pathlib import Path
from typing import Any, Dict, Optional

import requests


class LLMClient:
    """
    Unified LLM interface supporting:
      - ONLINE: Groq OpenAI-compatible API
      - LOCAL: Ollama via /api/generate (fallback to /api/chat if supported)

    SarahCore will call this with llm_mode="online" or "local".
    """

    def __init__(self, backend_root: Path):
        backend_root = Path(backend_root)
        self.backend_root = backend_root

        cfg = self._load_llm_config(backend_root)

        # -------------------------------------------
        # GROQ (online) configuration
        # -------------------------------------------
        self.groq_model: str = cfg.get("groq_model", "llama-3.1-8b-instant")

        # WHICH ENV VARIABLE holds your Groq API key
        self.groq_api_key_env: str = cfg.get("groq_api_key_env", "GROQ_API_KEY")

        # The actual API key (loaded from environment)
        self.groq_api_key: str = "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"


        # -------------------------------------------
        # OLLAMA (local) configuration
        # -------------------------------------------
        self.ollama_base_url: str = os.getenv(
            "OLLAMA_BASE_URL", "http://127.0.0.1:11434"
        ).rstrip("/")

        # Default local model
        self.default_local_model: str = os.getenv(
            "SARAH_LOCAL_MODEL", "dolphin-mixtral"
        )

        print(f"[LLM INIT] Online model: {self.groq_model}")
        print(f"[LLM INIT] Default local model: {self.default_local_model}")
        print(f"[LLM INIT] Groq API Key loaded: {'YES' if self.groq_api_key else 'NO'}")


    # ---------------------------------------------------------------
    # CONFIG LOADING
    # ---------------------------------------------------------------
    def _load_llm_config(self, backend_root: Path) -> Dict[str, Any]:
        cfg_path = backend_root / "config_sarah_v11.json"
        if not cfg_path.exists():
            # fall back to the pre-V11 config name
            cfg_path = backend_root / "config_sarah_v8.json"
        if not cfg_path.exists():
            return {}

        try:
            raw = json.loads(cfg_path.read_text(encoding="utf-8"))
        except Exception:
            return {}

        return raw.get("llm", {})


    # ---------------------------------------------------------------
    # MAIN UNIFIED ENTRY POINT
    # ---------------------------------------------------------------
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

        llm_mode = (kwargs.get("llm_mode") or os.getenv("SARAH_LLM_MODE", "online")).lower()
        local_model = kwargs.get("local_model") or self.default_local_model

        print(f"[LLM] generate(mode={llm_mode}, local_model={local_model})")

        try:
            # --------------------------
            # LOCAL (Ollama)
            # --------------------------
            if llm_mode == "local":
                return self._call_ollama_generate(
                    system_prompt=system_prompt,
                    user_message=user_message,
                    model=local_model,
                )

            # --------------------------
            # ONLINE (Groq)
            # --------------------------
            return self._call_groq_chat(system_prompt, user_message)

        except Exception as e:
            print(f"[LLM ERROR] Primary LLM failed (mode={llm_mode}): {e}")

            # fallback: online → offline? offline → online?
            if llm_mode == "local":
                print("[LLM] Falling back to Groq after Ollama failure.")
                try:
                    return self._call_groq_chat(system_prompt, user_message)
                except Exception as e2:
                    print(f"[LLM ERROR] Groq fallback failed: {e2}")
                    return ""

            return ""


    # ---------------------------------------------------------------
    # ONLINE LLM — GROQ
    # ---------------------------------------------------------------
    def _call_groq_chat(self, system_prompt: str, user_message: str) -> str:
        if not self.groq_api_key:
            print(f"[LLM WARNING] {self.groq_api_key_env} not set; cannot call Groq.")
            return ""

        url = "https://api.groq.com/openai/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.groq_api_key}",
            "Content-Type": "application/json",
        }

        payload = {
            "model": self.groq_model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user",  "content": user_message},
            ],
            "temperature": 0.7,
            "max_tokens": 1024,
        }

        print(f"[LLM GROQ] Calling Groq model={self.groq_model}")
        resp = requests.post(url, json=payload, headers=headers, timeout=60)
        resp.raise_for_status()
        data = resp.json()

        try:
            content = data["choices"][0]["message"]["content"]
        except Exception as e:
            print("[LLM ERROR] Unexpected Groq response:", e, data)
            return ""

        return (content or "").strip()


    # ---------------------------------------------------------------
    # LOCAL LLM — OLLAMA
    # ---------------------------------------------------------------
    def _call_ollama_generate(
        self,
        system_prompt: str,
        user_message: str,
        model: str,
    ) -> str:

        # Format prompt for /api/generate
        prompt = f"{system_prompt}\nUser: {user_message}\nAssistant:"

        payload = {
            "model": model,
            "prompt": prompt,
            "stream": False,
        }

        url = f"{self.ollama_base_url}/api/generate"

        print(f"[LLM OLLAMA] Calling Ollama model={model} at {url}")

        resp = requests.post(url, json=payload, timeout=300)

        # If Ollama returns an error code
        try:
            resp.raise_for_status()
        except Exception as e:
            print("[LLM ERROR] Ollama returned HTTP error:", e)
            raise

        data = resp.json()

        # Expected format:
        # { "model": "...", "response": "text here", ... }
        response_text = data.get("response", "")
        return (response_text or "").strip()


    # ---------------------------------------------------------------
    # OPTIONAL: For future
    # ---------------------------------------------------------------
    def set_mode(self, llm_mode: str, local_model: str):
        """
        Called by SarahCore when toggling local/online.
        Safe and optional.
        """
        print(f"[LLM] set_mode({llm_mode}, {local_model})")
        self._mode = llm_mode
        self._local_model = local_model
