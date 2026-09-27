# backend/memory/openrouter_client.py
"""
OpenRouter Client (memory-integrated)
=====================================
LLM client with integrated memory system support, using the OpenAI SDK
pointed at OpenRouter. The model is `openrouter/auto` (auto-routed per prompt).

Features:
- Uses ContextBuilder for intelligent context management
- Token budget enforcement
- Automatic summarization triggers
- Intent resolution for short replies
- Vision observation injection

All memory operations are SILENT - never shown to user.
"""

import os
import asyncio
from typing import Optional, List, Dict, Any
from dataclasses import dataclass

import requests

from backend.config import settings as _settings
from backend.identity import get_user_name
from backend.reply_sanitizer import sanitize_visible_reply
from .config import MemoryConfig, get_memory_config
from .memory_store import MemoryStore, get_memory_store
from .summarizer import Summarizer
from .context_builder import ContextBuilder, LLMContextPacket


# The event loop only keeps weak references to tasks, so a fire-and-forget
# `asyncio.create_task(...)` can be garbage-collected before it finishes.
_background_tasks: "set[asyncio.Task]" = set()


def spawn_background(coro) -> "asyncio.Task":
    """Schedule `coro` without awaiting it, keeping it alive until done."""
    task = asyncio.create_task(coro)
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
    return task


@dataclass
class LLMResponse:
    """Response from the LLM with metadata."""
    content: str
    model: str
    usage: Dict[str, int]  # prompt_tokens, completion_tokens, total_tokens
    finish_reason: str
    debug_info: Dict[str, Any] = None
    user_message_id: Optional[int] = None
    assistant_message_id: Optional[int] = None

    def __post_init__(self):
        if self.debug_info is None:
            self.debug_info = {}


class OpenRouterClient:
    """
    Memory-integrated LLM client backed by OpenRouter.

    Usage:
        client = OpenRouterClient()
        response = await client.chat(conversation_id, user_message)
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        store: Optional[MemoryStore] = None,
        config: Optional[MemoryConfig] = None,
    ):
        self.config = config or get_memory_config()
        self.store = store or get_memory_store()

        self.api_key = (
            api_key
            or _settings.openrouter_api_key
            or os.environ.get("OPENROUTER_API_KEY", "")
        )

        self._client = None
        self._init_client()

        self.context_builder = ContextBuilder(
            store=self.store,
            config=self.config,
        )
        self.summarizer: Optional[Summarizer] = None

        self._llm_mode_info: Dict[str, Any] = {
            "mode": "online",
            "model_name": self.config.llm_model,
            "provider": "OpenRouter",
            "token_budget": _settings.openrouter_context_window_tokens,
            "completion_token_budget": self.config.llm_max_completion_tokens,
        }

        if self.config.debug_memory:
            print(f"[OpenRouterClient] Initialized with model={self.config.llm_model}")

    def set_llm_mode_info(self, mode: str, model_name: str, provider: str = ""):
        """Update the current LLM mode info for model awareness."""
        if not provider:
            provider = "OpenRouter" if mode == "online" else "Ollama"

        token_budget = _settings.local_effective_context_tokens if mode == "local" else _settings.openrouter_context_window_tokens
        completion_token_budget = (
            min(_settings.local_max_completion_tokens, self.config.llm_max_completion_tokens)
            if mode == "local"
            else self.config.llm_max_completion_tokens
        )

        self._llm_mode_info = {
            "mode": mode,
            "model_name": model_name,
            "provider": provider,
            "token_budget": token_budget,
            "completion_token_budget": completion_token_budget,
        }
        print(
            f"[OpenRouterClient] LLM mode updated: {mode} / {model_name} / {provider} "
            f"(context={token_budget}, completion={completion_token_budget})"
        )

    def _init_client(self):
        """Initialize the OpenAI SDK client pointed at OpenRouter."""
        try:
            from openai import OpenAI
            self._client = OpenAI(
                api_key=self.api_key,
                base_url=_settings.openrouter_base_url,
            )
            print("[OpenRouterClient] OpenAI SDK client initialized (base_url=OpenRouter)")
        except ImportError:
            print("[OpenRouterClient] WARNING: openai package not installed")
            self._client = None
        except Exception as e:
            print(f"[OpenRouterClient] WARNING: Failed to init client: {e}")
            self._client = None

    def _ensure_summarizer(self):
        if self.summarizer is None:
            self.summarizer = Summarizer(
                llm_call_fn=self._llm_call_for_summarizer,
                store=self.store,
                config=self.config,
            )

    def _is_local_mode(self) -> bool:
        return str(self._llm_mode_info.get("mode", "online")).lower() == "local"

    def _current_model_name(self) -> str:
        if self._is_local_mode():
            return str(self._llm_mode_info.get("model_name") or _settings.default_local_model)
        return self.config.llm_model

    def _current_completion_budget(self) -> int:
        try:
            value = int(self._llm_mode_info.get("completion_token_budget") or 0)
        except Exception:
            value = 0
        if value <= 0:
            value = _settings.local_max_completion_tokens if self._is_local_mode() else self.config.llm_max_completion_tokens
        return max(32, value)

    def _call_ollama_chat_sync(
        self,
        messages: List[Dict[str, str]],
        max_tokens: int,
        temperature: float,
    ) -> Dict[str, Any]:
        """Call local Ollama using its OpenAI-like chat payload shape."""
        url = f"{_settings.ollama_base_url.rstrip('/')}/api/chat"
        payload = {
            "model": self._current_model_name(),
            "messages": messages,
            "stream": False,
            "options": {
                "num_predict": max_tokens,
                "temperature": min(temperature, _settings.local_temperature),
                "num_ctx": _settings.local_context_window_tokens,
                "stop": list(_settings.local_stop_sequences),
            },
            "keep_alive": "10m",
        }
        resp = requests.post(url, json=payload, timeout=_settings.local_request_timeout_seconds)
        resp.raise_for_status()
        data = resp.json()

        message = data.get("message") if isinstance(data.get("message"), dict) else {}
        content = (
            message.get("content")
            or data.get("response")
            or data.get("text")
            or ""
        )
        prompt_tokens = int(data.get("prompt_eval_count") or 0)
        completion_tokens = int(data.get("eval_count") or 0)
        total_tokens = prompt_tokens + completion_tokens
        if total_tokens <= 0:
            total_tokens = int(len(content) / self.config.chars_per_token)

        return {
            "content": content,
            "model": data.get("model") or self._current_model_name(),
            "usage": {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": total_tokens,
            },
            "finish_reason": data.get("done_reason") or "stop",
        }

    async def _llm_call_for_summarizer(self, prompt: str, max_tokens: int) -> str:
        if self._is_local_mode():
            return await self.simple_completion(prompt, max_tokens=max_tokens, temperature=0.3)

        if not self.api_key or not self._client:
            return ""

        loop = asyncio.get_running_loop()

        def _call():
            resp = self._client.chat.completions.create(
                model=self.config.llm_model,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=max_tokens,
                temperature=0.3,
            )
            return resp.choices[0].message.content or ""

        try:
            return await loop.run_in_executor(None, _call)
        except Exception as e:
            print(f"[OpenRouterClient] Summarizer completion error: {e}")
            return ""

    async def chat(
        self,
        conversation_id: int,
        user_message: str,
        vision_observation: Optional[str] = None,
        save_messages: bool = True,
        save_user_message: bool = True,
        project_context: Optional[str] = None,
    ) -> LLMResponse:
        use_local = self._is_local_mode()

        if not use_local and not self._client:
            return LLMResponse(
                content="I'm sorry, the AI service is not configured properly.",
                model=self.config.llm_model,
                usage={"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
                finish_reason="error",
                debug_info={"error": "OpenRouter client not initialized"},
            )

        print(f"[OpenRouterClient] chat() called - conversation_id={conversation_id}, save_messages={save_messages}, save_user_message={save_user_message}")

        packet = self.context_builder.build(
            conversation_id=conversation_id,
            user_message=user_message,
            vision_observation=vision_observation,
            llm_mode_info=self._llm_mode_info,
            project_context=project_context,
        )
        print(f"[OpenRouterClient] Context packet built, {len(packet.messages)} messages")

        user_message_id = None
        assistant_message_id = None

        if save_messages and save_user_message:
            from backend.models.core import add_message
            import json

            meta_json = None
            if vision_observation:
                meta_json = json.dumps({
                    "vision_observation": vision_observation,
                    "has_image": True
                })

            user_message_id = add_message(conversation_id, "user", user_message, meta_json=meta_json)

        loop = asyncio.get_running_loop()

        try:
            if use_local:
                def _call_local():
                    return self._call_ollama_chat_sync(
                        packet.messages,
                        max_tokens=self._current_completion_budget(),
                        temperature=self.config.llm_temperature,
                    )

                local_response = await loop.run_in_executor(None, _call_local)
                content = local_response["content"]
                finish_reason = local_response["finish_reason"]
                usage = local_response["usage"]
                model_name = local_response["model"]
                provider = "Ollama"
            else:
                def _call_openrouter():
                    extra_body = {}
                    effort = (_settings.openrouter_reasoning_effort or "").strip().lower()
                    if effort and effort != "none":
                        extra_body["reasoning"] = {"effort": effort, "exclude": True}
                    resp = self._client.chat.completions.create(
                        model=self.config.llm_model,
                        messages=packet.messages,
                        max_tokens=self.config.llm_max_completion_tokens,
                        temperature=self.config.llm_temperature,
                        extra_body=extra_body or None,
                    )
                    return resp

                response = await loop.run_in_executor(None, _call_openrouter)

                content = response.choices[0].message.content or ""
                finish_reason = response.choices[0].finish_reason or "stop"

                usage = {
                    "prompt_tokens": response.usage.prompt_tokens if response.usage else 0,
                    "completion_tokens": response.usage.completion_tokens if response.usage else 0,
                    "total_tokens": response.usage.total_tokens if response.usage else 0,
                }
                model_name = self.config.llm_model
                provider = "OpenRouter"

            content = sanitize_visible_reply(content) or f"I'm here, {get_user_name()}."

            if save_messages and content:
                from backend.models.core import add_message
                assistant_message_id = add_message(conversation_id, "assistant", content)

            self._ensure_summarizer()
            spawn_background(self.summarizer.update_all(conversation_id, content))

            result = LLMResponse(
                content=content,
                model=model_name,
                usage=usage,
                finish_reason=finish_reason,
                debug_info={
                    **packet.debug_info,
                    "actual_prompt_tokens": usage["prompt_tokens"],
                    "llm_provider": provider,
                    "llm_mode": "local" if use_local else "online",
                },
                user_message_id=user_message_id,
                assistant_message_id=assistant_message_id,
            )

            if self.config.debug_memory:
                print(f"[OpenRouterClient] Response: {len(content)} chars, {usage['total_tokens']} tokens")

            return result

        except Exception as e:
            print(f"[OpenRouterClient] API error: {e}")
            return LLMResponse(
                content=f"I encountered an error processing your request. Please try again.",
                model=self.config.llm_model,
                usage={"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
                finish_reason="error",
                debug_info={"error": str(e)},
                # The user turn is already persisted; hand back its id so the
                # UI can still edit/delete/regenerate it instead of orphaning it.
                user_message_id=user_message_id,
            )

    async def simple_completion(
        self,
        prompt: str,
        max_tokens: int = 512,
        temperature: float = 0.7,
    ) -> str:
        if self._is_local_mode():
            loop = asyncio.get_running_loop()

            def _call_local():
                return self._call_ollama_chat_sync(
                    [{"role": "user", "content": prompt}],
                    max_tokens=max_tokens,
                    temperature=temperature,
                )["content"]

            try:
                return await loop.run_in_executor(None, _call_local)
            except Exception as e:
                print(f"[OpenRouterClient] Local simple completion error: {e}")
                return ""

        if not self._client:
            return ""

        loop = asyncio.get_running_loop()

        def _call():
            resp = self._client.chat.completions.create(
                model=self.config.llm_model,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=max_tokens,
                temperature=temperature,
            )
            return resp.choices[0].message.content or ""

        try:
            return await loop.run_in_executor(None, _call)
        except Exception as e:
            print(f"[OpenRouterClient] Simple completion error: {e}")
            return ""

    def get_context_debug_info(self, conversation_id: int, user_message: str) -> Dict[str, Any]:
        packet = self.context_builder.build(
            conversation_id=conversation_id,
            user_message=user_message,
            llm_mode_info=self._llm_mode_info,
        )
        return {
            "estimated_tokens": packet.estimated_tokens,
            "message_count": len(packet.messages),
            "llm_mode_info": self._llm_mode_info,
            **packet.debug_info,
        }


# ============================================================
# SINGLETON AND CONVENIENCE FUNCTIONS
# ============================================================

_openrouter_client: Optional[OpenRouterClient] = None


def get_openrouter_client(
    api_key: Optional[str] = None,
    store: Optional[MemoryStore] = None,
    config: Optional[MemoryConfig] = None,
) -> OpenRouterClient:
    """Get or create global OpenRouterClient instance."""
    global _openrouter_client
    if _openrouter_client is None:
        _openrouter_client = OpenRouterClient(
            api_key=api_key,
            store=store,
            config=config,
        )
    return _openrouter_client


def reset_openrouter_client():
    """Reset client singleton (for testing)."""
    global _openrouter_client
    _openrouter_client = None


async def chat_with_memory(
    conversation_id: int,
    user_message: str,
    vision_observation: Optional[str] = None,
) -> str:
    """Convenience function for chat with full memory support."""
    client = get_openrouter_client()
    response = await client.chat(
        conversation_id=conversation_id,
        user_message=user_message,
        vision_observation=vision_observation,
    )
    return response.content
