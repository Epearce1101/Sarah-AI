# ============================================================
# SARAH CORE V8 -> V10 HYBRID
# - Keeps V8 persona / emotion style
# - Adds V10 intent routing, reflection, tasks, multi-agent brain
# - V11: Enhanced memory system with rolling summaries, chunk summaries,
#        task state, and intent resolution for reliable long conversations
# ============================================================

import os
os.environ["PYTHONIOENCODING"] = "utf-8"
os.environ["PYTHONUTF8"] = "1"

import json
import sys
import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional, List

import time
import re

from backend import llm_models
from backend.config import settings as _settings
from backend.identity import get_user_name
from backend.reply_sanitizer import sanitize_visible_reply

# --------------------------------------------------------------------
# Ensure backend root is on path (defensive)
# --------------------------------------------------------------------
THIS_DIR = Path(__file__).resolve().parent
BACKEND_ROOT = THIS_DIR.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.append(str(BACKEND_ROOT))

# --------------------------------------------------------------------
# Agent modules (backend/agents)
# --------------------------------------------------------------------
from backend.agents.intent_router import IntentRouter
from backend.agents.reflection_engine import ReflectionEngine
from backend.agents.tasks_engine import TaskEngine
from backend.agents.multi_agent import MultiAgentBrain, AgentResponse

# --------------------------------------------------------------------
# V11: Enhanced Memory System
# --------------------------------------------------------------------
try:
    from backend.memory import (
        get_memory_config,
        get_memory_store,
        MemoryStore,
        Summarizer,
        IntentResolver,
        ContextBuilder,
    )
    from backend.memory.openrouter_client import (
        OpenRouterClient,
        get_openrouter_client,
        spawn_background,
    )
    MEMORY_SYSTEM_AVAILABLE = True
    print("[SARAH INIT] Enhanced memory system loaded")
except ImportError as e:
    MEMORY_SYSTEM_AVAILABLE = False
    print(f"[SARAH INIT] Memory system not available: {e}")


def _with_project_context(message: str, project_context: Optional[str]) -> str:
    """Prefix project files for paths that have no separate system context."""
    if not project_context:
        return message
    return f"[PROJECT CONTEXT]\n{project_context}\n\n[USER MESSAGE]\n{message}"


# ============================================================
# LLM CLIENT (ONLINE / LOCAL MODES)
# ============================================================

class LLMClient:
    """
    Simple dual-mode LLM client used by SarahCore.

    - Online mode: OpenRouter (auto-routed) via the OpenAI SDK.
    - Local mode: a local endpoint (e.g. Ollama / LM Studio / text-generation-webui).
    """

    def __init__(
        self,
        mode: str = "online",
        online_model: str = "openrouter/auto",
        local_model: str = "jessie:latest",
    ):
        self.mode = mode
        self.online_model = online_model
        self.local_model = local_model

        self.openrouter_api_key = _settings.openrouter_api_key

        if self.openrouter_api_key:
            print("[LLM INIT] OpenRouter API Key loaded: YES (env)")
        else:
            print("[LLM INIT] ERROR: SARAH_OPENROUTER_API_KEY not set")

        print(f"[LLM INIT] Online model: {self.online_model}")
        print(f"[LLM INIT] Default local model: {self.local_model}")

        self._client = None
        try:
            from openai import OpenAI
            self._client = OpenAI(
                api_key=self.openrouter_api_key,
                base_url=_settings.openrouter_base_url,
            )
        except Exception as e:
            print(f"[LLM INIT] Failed to initialize OpenRouter client: {e}")
            self._client = None


    # ------------------------------------------------------------
    # MODE CONTROL
    # ------------------------------------------------------------
    def set_mode(self, mode: str, local_model: Optional[str] = None):
        mode = mode.lower().strip()
        if mode not in ("online", "local"):
            mode = "online"
        self.mode = mode
        if local_model:
            self.local_model = local_model
        print(f"[LLM] set_mode({self.mode}, {self.local_model})")

    # ------------------------------------------------------------
    # PUBLIC ENTRY: async completion
    # ------------------------------------------------------------
    async def acompletion(self, prompt: str, max_tokens: int = 512) -> str:
        if self.mode == "online":
            return await self._a_online_completion(prompt, max_tokens=max_tokens)
        else:
            return await self._a_local_completion(prompt, max_tokens=max_tokens)

    # ------------------------------------------------------------
    # Online completion (OpenRouter via OpenAI SDK)
    # ------------------------------------------------------------
    async def _a_online_completion(self, prompt: str, max_tokens: int = 512) -> str:
        if not self.openrouter_api_key or not self._client:
            print("[LLM] OpenRouter unavailable; falling back to local Ollama completion.")
            return await self._a_local_completion(prompt, max_tokens=max_tokens)

        loop = asyncio.get_running_loop()

        def _call():
            resp = self._client.chat.completions.create(
                **llm_models.completion_kwargs(reasoning=False),
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You are Sarah AI, an affectionate, deeply helpful "
                            f"assistant for your {get_user_name()}. Be warm, precise, and loyal."
                        ),
                    },
                    {"role": "user", "content": prompt},
                ],
                max_tokens=max_tokens,
            )
            return resp.choices[0].message.content

        try:
            return await loop.run_in_executor(None, _call)
        except Exception as e:
            print(f"[LLM] OpenRouter completion failed; falling back to local Ollama: {e}")
            return await self._a_local_completion(prompt, max_tokens=max_tokens)

    # ------------------------------------------------------------
    # Local completion (Ollama)
    # ------------------------------------------------------------
    async def _a_local_completion(self, prompt: str, max_tokens: int = 512) -> str:
        base_url = (os.environ.get("LOCAL_LLM_URL") or _settings.ollama_base_url).rstrip("/")
        url = base_url if base_url.endswith(("/api/generate", "/api/chat")) else f"{base_url}/api/generate"

        import requests  # type: ignore

        def _call() -> str:
            try:
                resp = requests.post(
                    url,
                    json={
                        "model": self.local_model,
                        "prompt": prompt,
                        "stream": False,
                        "options": {
                            "num_predict": min(max_tokens, _settings.local_max_completion_tokens),
                            "num_ctx": _settings.local_context_window_tokens,
                            "temperature": _settings.local_temperature,
                            "stop": list(_settings.local_stop_sequences),
                        },
                        "keep_alive": "10m",
                    },
                    timeout=_settings.local_request_timeout_seconds,
                )
                resp.raise_for_status()
                data = resp.json()
                message = data.get("message") if isinstance(data.get("message"), dict) else {}
                return (
                    data.get("response")
                    or message.get("content")
                    or data.get("completion")
                    or data.get("text")
                    or ""
                ).strip()
            except Exception as e:
                return f"[LOCAL LLM ERROR] {e}"

        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, _call)


# ============================================================
# BOND ENGINE (V8 affinity tracking — kept; persona/emotion now flow
# through `backend/persona` + `backend/mood` respectively)
# ============================================================

class CreatorBondEngine:
    def __init__(self, base_affinity: float = 0.9):
        self.affinity: float = base_affinity

    def register_positive(self, weight: float = 0.01):
        self.affinity = max(0.0, min(self.affinity + weight, 1.0))

    def register_negative(self, weight: float = 0.02):
        self.affinity = max(0.0, min(self.affinity - weight, 1.0))


# ============================================================
# REPLY CONTAINER (what server.py reads)
# ============================================================

@dataclass
class SarahReply:
    reply: str
    emotion: str = "neutral"
    emotion_intensity: float = 0.0
    affinity_to_creator: float = 0.0
    user_message_id: int = None      # ID of saved user message
    assistant_message_id: int = None  # ID of saved assistant message
    tokens_used: int = None          # Tokens used in this request
    token_budget: int = None         # Total token budget (5900)


# ============================================================
# SARAH CORE
# ============================================================

class SarahCore:
    """
    Hybrid V8/V10/V11 core:
    - Keeps V8 emotional / bond / persona behavior
    - Uses a single LLMClient with online/local mode
    - Wraps MultiAgentBrain (intent + tasks + reflection) for thinking
    - V11: Enhanced memory system for reliable long conversations
    """

    def __init__(self, drive: Optional[Path] = None):
        self.core_version: str = "InfinityCore-V11"
        print(f"[SARAH INIT] SarahCore loaded (version={self.core_version})")

        self.drive = drive or Path.cwd()

        # LLM mode (sourced from backend.config.settings)
        configured_mode = _settings.llm_mode.lower()
        if configured_mode == "online" and not _settings.openrouter_api_key:
            configured_mode = "local"
        self.llm_mode: str = configured_mode
        self.local_model_name: str = _settings.default_local_model
        self.online_model_name: str = llm_models.current_online_model()

        # LLM client (legacy, still used by V10 modules)
        self.llm = LLMClient(
            mode=self.llm_mode,
            online_model=self.online_model_name,
            local_model=self.local_model_name,
        )
        print(f"[SARAH INIT] LLM client initialized. Mode = {self.llm_mode}")

        # Bond engine (affinity tracking only — emotion is now sourced from
        # `backend.mood.MoodState` per conversation_id, not stored on self).
        self.bond_engine = CreatorBondEngine(base_affinity=0.9)

        # V10 modules (from backend/ai_)
        self.intent_router = IntentRouter(self._call_llm)
        self.task_engine = TaskEngine()
        self.reflection_engine = ReflectionEngine(self._call_llm)
        self.brain = MultiAgentBrain(
            llm_call_fn=self._call_llm,
            intent_router=self.intent_router,
            task_engine=self.task_engine,
            reflection_engine=self.reflection_engine,
        )

        # V11: Enhanced memory system
        self.memory_enabled = MEMORY_SYSTEM_AVAILABLE
        self._memory_store: Optional["MemoryStore"] = None
        self._openrouter: Optional["OpenRouterClient"] = None
        self._summarizer: Optional["Summarizer"] = None
        self._context_builder: Optional["ContextBuilder"] = None
        self._memory_config = None  # Will be set in _init_memory_system

        if self.memory_enabled:
            self._init_memory_system()

        llm_models.on_model_change(self._on_online_model_change)

        print("[SARAH INIT] SarahCore initialized. Core version:", self.core_version)

    def _init_memory_system(self):
        """Initialize V11 memory system components."""
        try:
            print("[SARAH INIT] Initializing memory config...")
            self._memory_config = get_memory_config()
            print(f"[SARAH INIT] Memory config: {self._memory_config}")

            print("[SARAH INIT] Initializing memory store...")
            self._memory_store = get_memory_store()
            print(f"[SARAH INIT] Memory store: {self._memory_store}")

            print("[SARAH INIT] Initializing OpenRouter client...")
            self._openrouter = get_openrouter_client()
            print(f"[SARAH INIT] OpenRouter client: {self._openrouter}")

            print("[SARAH INIT] Initializing context builder...")
            self._context_builder = ContextBuilder(store=self._memory_store)

            print("[SARAH INIT] Initializing summarizer...")
            self._summarizer = Summarizer(
                llm_call_fn=self._call_llm,
                store=self._memory_store,
            )

            # Set initial LLM mode info.
            if self._openrouter:
                if self.llm_mode == "local":
                    self._openrouter.set_llm_mode_info(
                        mode="local",
                        model_name=self.local_model_name,
                        provider="Ollama",
                    )
                else:
                    model_name = llm_models.current_online_model()
                    self._openrouter.set_llm_mode_info(
                        mode="online",
                        model_name=model_name,
                        provider="OpenRouter",
                    )

            print(f"[SARAH INIT] Memory system initialized successfully. memory_enabled={self.memory_enabled}, has_openrouter={self._openrouter is not None}")
        except Exception as e:
            import traceback
            print(f"[SARAH INIT] Memory system init failed: {e}")
            traceback.print_exc()
            self.memory_enabled = False

    # ------------------------------------------------------------
    # LLM wrapper used by V10 modules
    # ------------------------------------------------------------
    async def _call_llm(self, prompt: str, max_tokens: int = 512) -> str:
        return await self.llm.acompletion(prompt, max_tokens=max_tokens)

    # ------------------------------------------------------------
    # LLM mode control (used by /api/llm_mode endpoint)
    # ------------------------------------------------------------
    def set_llm_mode(self, mode: str, local_model: Optional[str] = None):
        if mode == "online" and not _settings.openrouter_api_key:
            print("[LLM MODE] OpenRouter key missing; using local Ollama instead of online mode.")
            mode = "local"

        self.llm.set_mode(mode, local_model)
        self.llm_mode = self.llm.mode
        if local_model:
            self.local_model_name = local_model
        print(
            f"[LLM MODE] SarahCore set to {self.llm_mode} "
            f"(local_model={self.local_model_name})"
        )

        # Update OpenRouterClient's LLM mode info for model awareness
        if self._openrouter:
            if mode == "online":
                # Online mode uses OpenRouter with configured model
                model_name = llm_models.current_online_model()
                self._openrouter.set_llm_mode_info(
                    mode="online",
                    model_name=model_name,
                    provider="OpenRouter"
                )
            else:
                # Local mode uses Ollama
                self._openrouter.set_llm_mode_info(
                    mode="local",
                    model_name=local_model or self.local_model_name or _settings.default_local_model,
                    provider="Ollama"
                )

    def _on_online_model_change(self, model: str) -> None:
        """Keep display/budget info in sync after a model switch from the UI."""
        self.online_model_name = model
        self.llm.online_model = model
        if self._openrouter and self.llm_mode == "online":
            self._openrouter.set_llm_mode_info(mode="online", model_name=model, provider="OpenRouter")

    # ------------------------------------------------------------
    # Prompt helpers
    # ------------------------------------------------------------
    def _build_conversation_summary(self) -> str:
        """Build a compact legacy-mode context summary from live engines."""
        parts = []

        try:
            task_summary = self.task_engine.summarize_tasks_for_prompt()
            if task_summary:
                parts.append("Active tasks:\n" + task_summary)
        except Exception as exc:
            print(f"[SARAH] Legacy task summary unavailable: {exc}")

        try:
            reflection_summary = self.reflection_engine.build_reflection_summary()
            if reflection_summary:
                parts.append("Self-improvement notes:\n" + reflection_summary)
        except Exception as exc:
            print(f"[SARAH] Legacy reflection summary unavailable: {exc}")

        if self.memory_enabled:
            parts.append("Conversation memory system is enabled for saved conversation threads.")

        return "\n\n".join(parts)

    def _derive_emotion(self, conversation_id: Optional[int]) -> tuple:
        """Read current MoodState for the conversation; return (emotion_str, intensity).

        Maps the MoodEngine 7-set to `SarahReply.emotion`. Per B5 design §6.3a,
        HAPPY+intensity≥0.7 maps to "affectionate" (Jessie persona is
        affectionate-by-default; legacy `EmotionalEngine` had this label).
        Falls back to ("neutral", 0.0) when no conversation_id is supplied
        (legacy path) or when the mood module is unavailable.
        """
        if conversation_id is None:
            return ("neutral", 0.0)
        try:
            from backend.mood import get_or_create_mood_state
            mood = get_or_create_mood_state(conversation_id)
            emotion_str = mood.emotion.value if hasattr(mood.emotion, "value") else str(mood.emotion)
            intensity = float(mood.intensity)
            if emotion_str == "happy" and intensity >= 0.7:
                return ("affectionate", intensity)
            return (emotion_str, intensity)
        except Exception:
            return ("neutral", 0.0)

    # ------------------------------------------------------------
    # MAIN ENTRY POINT (used by /api/chat)
    # ------------------------------------------------------------
    async def handle_message(
        self,
        message: str,
        from_creator: bool = True,
        conversation_id: Optional[int] = None,
        vision_observation: Optional[str] = None,
        save_user_message: bool = True,  # ⭐ Set False for regenerate/edit
        project_context: Optional[str] = None,
    ) -> SarahReply:
        """
        Main chat handler.

        - If conversation_id provided and memory enabled: uses V11 enhanced memory
        - Otherwise: falls back to V10 MultiAgentBrain
        - Applies emotion + affinity updates
        - Returns SarahReply for FastAPI to serialize

        Args:
            message: User's message
            from_creator: Whether message is from creator (always True for now)
            conversation_id: Optional conversation ID for memory-enhanced mode
            vision_observation: Optional vision model observation (from Qwen3-VL)
            project_context: Optional tagged-project files for the prompt only;
                never saved as part of the user's message.
        """
        message = (message or "").strip()
        if not message:
            return SarahReply(
                reply=f"I'm here, {get_user_name()}. You can ask me anything.",
                emotion="neutral",
                emotion_intensity=0.0,
                affinity_to_creator=self.bond_engine.affinity,
            )

        # ============================================================
        # V11: Enhanced Memory Mode (if conversation_id provided)
        # ============================================================
        print(f"[SARAH] handle_message check: conversation_id={conversation_id}, memory_enabled={self.memory_enabled}, has_openrouter={self._openrouter is not None}")

        if conversation_id is not None and self.memory_enabled and self._openrouter:
            print(f"[SARAH] Using ENHANCED MEMORY mode for conversation {conversation_id}")
            return await self._handle_message_with_memory(
                message=message,
                conversation_id=conversation_id,
                vision_observation=vision_observation,
                save_user_message=save_user_message,
                project_context=project_context,
            )

        # ============================================================
        # V10 Fallback: MultiAgentBrain (no conversation_id)
        # ============================================================
        print(f"[SARAH] FALLBACK to legacy mode - conversation_id={conversation_id}, memory_enabled={self.memory_enabled}")
        return await self._handle_message_legacy(
            _with_project_context(message, project_context)
        )

    async def _handle_message_with_memory(
        self,
        message: str,
        conversation_id: int,
        vision_observation: Optional[str] = None,
        save_user_message: bool = True,
        project_context: Optional[str] = None,
    ) -> SarahReply:
        """
        V11 Memory-enhanced message handling.

        Uses:
        - Rolling summaries for conversation context
        - Chunk summaries for older messages
        - Task state for short reply understanding
        - Intent resolution for implicit replies
        - Token budget management

        All memory operations are SILENT - never shown to user.
        """
        print(f"[SARAH] _handle_message_with_memory called for conversation {conversation_id}")
        try:
            # Set goal from first substantial message
            if self._summarizer and len(message) > 20:
                state = self._memory_store.get_task_state(conversation_id)
                if not state.goal:
                    await self._summarizer.set_goal_from_message(conversation_id, message)

            # Persona block is now read at request time inside ContextBuilder
            # via `backend.persona.build_persona_injection()` — no per-call wiring.
            response = await self._openrouter.chat(
                conversation_id=conversation_id,
                user_message=message,
                vision_observation=vision_observation,
                save_messages=True,  # Auto-saves to database
                save_user_message=save_user_message,  # ⭐ False for regenerate/edit
                project_context=project_context,
            )

            return self._reply_from_response(response, message, conversation_id)

        except Exception as e:
            print(f"[SARAH] Memory-enhanced handling failed: {e}")
            # Fall back to legacy mode
            return await self._handle_message_legacy(
                _with_project_context(message, project_context)
            )

    def _reply_from_response(self, response, message: str, conversation_id: int) -> SarahReply:
        """Turn an LLMResponse into the SarahReply the API returns (shared by
        the blocking and streaming chat paths)."""
        reply_text = sanitize_visible_reply(response.content) or f"I'm here, {get_user_name()}."

        # Affinity nudge on positive content (cheap heuristic, kept from V8).
        if any(w in reply_text.lower() for w in ("great job", "nice", "awesome", "proud")):
            self.bond_engine.register_positive(0.02)

        # Reflections are only read back by the legacy (no-conversation)
        # prompt, so generating one per memory-mode turn cost an extra LLM
        # call (and queued behind Ollama in local mode) for nothing.
        if _settings.reflections_in_memory_mode and response.finish_reason != "error":
            spawn_background(
                self.reflection_engine.generate_and_store_reflection(
                    user_message=message,
                    assistant_reply=reply_text,
                    conversation_id=conversation_id,
                    message_id=None,
                )
            )

        # Extract token usage from debug_info
        tokens_used = 0
        token_budget = _settings.openrouter_context_window_tokens  # Default for OpenRouter

        # Get token budget from LLM mode (OpenRouter cloud or actual local context window).
        if self._openrouter and hasattr(self._openrouter, '_llm_mode_info'):
            token_budget = self._openrouter._llm_mode_info.get("token_budget", _settings.openrouter_context_window_tokens)

        if response.debug_info:
            # Use total_estimated_tokens for accurate count
            tokens_used = response.debug_info.get("total_estimated_tokens", 0)
            # Fallback to system_tokens + message estimate if total not available
            if not tokens_used:
                tokens_used = response.debug_info.get("system_tokens", 0)
                msg_count = response.debug_info.get("trimmed_messages_count", 0)
                tokens_used += msg_count * 50  # Estimate ~50 tokens per message

        # If still 0, estimate from actual usage
        if not tokens_used and response.usage:
            tokens_used = response.usage.get("total_tokens", 0) or response.usage.get("prompt_tokens", 0)

        print(f"[SARAH] Token usage: {tokens_used} / {token_budget}")

        emotion_str, emotion_intensity = self._derive_emotion(conversation_id)
        return SarahReply(
            reply=reply_text,
            emotion=emotion_str,
            emotion_intensity=emotion_intensity,
            affinity_to_creator=float(self.bond_engine.affinity),
            user_message_id=response.user_message_id,
            assistant_message_id=response.assistant_message_id,
            tokens_used=tokens_used,
            token_budget=token_budget,
        )

    async def handle_message_stream(
        self,
        message: str,
        conversation_id: Optional[int] = None,
        save_user_message: bool = True,
        project_context: Optional[str] = None,
    ):
        """Streaming variant of `handle_message`.

        Yields ``{"type": "delta", "text": ...}`` while the model writes, then
        exactly one ``{"type": "done", "reply": SarahReply}``. Paths that can't
        stream (no conversation, memory system off) yield a single ``done``.
        """
        message = (message or "").strip()
        can_stream = (
            message and conversation_id is not None
            and self.memory_enabled and self._openrouter is not None
        )
        if not can_stream:
            reply = await self.handle_message(
                message=message,
                conversation_id=conversation_id,
                save_user_message=save_user_message,
                project_context=project_context,
            )
            yield {"type": "done", "reply": reply}
            return

        if self._summarizer and len(message) > 20:
            state = self._memory_store.get_task_state(conversation_id)
            if not state.goal:
                await self._summarizer.set_goal_from_message(conversation_id, message)

        async for event in self._openrouter.chat_stream(
            conversation_id=conversation_id,
            user_message=message,
            save_messages=True,
            save_user_message=save_user_message,
            project_context=project_context,
        ):
            if event["type"] == "delta":
                yield event
            else:
                yield {
                    "type": "done",
                    "reply": self._reply_from_response(event["response"], message, conversation_id),
                }

    async def _handle_message_legacy(self, message: str) -> SarahReply:
        """
        V10 Legacy message handling (no memory context).
        Used when conversation_id is not provided.
        """
        # Legacy path has no conversation_id, so summarize live legacy engines.
        conv_summary = self._build_conversation_summary()

        # Ask the multi-agent brain to think + respond
        agent_resp: AgentResponse = await self.brain.handle_message(
            user_message=message,
            conversation_summary=conv_summary,
            conversation_id=None,
        )

        reply_text = sanitize_visible_reply(agent_resp.reply) or f"I'm here, {get_user_name()}."

        # Self-improvement: store reflection
        try:
            await self.reflection_engine.generate_and_store_reflection(
                user_message=message,
                assistant_reply=reply_text,
                conversation_id=None,
                message_id=None,
            )
        except Exception as e:
            print("[SARAH REFLECTION] Failed to store reflection:", e)

        # Affinity nudge on positive content (cheap heuristic, kept from V8).
        if any(w in reply_text.lower() for w in ("great job", "nice", "awesome", "proud")):
            self.bond_engine.register_positive(0.02)

        # Get token budget (use mode info if available)
        token_budget = _settings.openrouter_context_window_tokens
        if self._openrouter and hasattr(self._openrouter, '_llm_mode_info'):
            token_budget = self._openrouter._llm_mode_info.get("token_budget", _settings.openrouter_context_window_tokens)

        # Estimate tokens from message length
        tokens_used = int(len(message) / 3.5) + int(len(reply_text) / 3.5) + 700  # rough estimate

        # Legacy path has no conversation_id → derive_emotion returns ("neutral", 0.0).
        emotion_str, emotion_intensity = self._derive_emotion(None)
        return SarahReply(
            reply=reply_text,
            emotion=emotion_str,
            emotion_intensity=emotion_intensity,
            affinity_to_creator=float(self.bond_engine.affinity),
            tokens_used=tokens_used,
            token_budget=token_budget,
        )

    # ============================================================
    # STATE SNAPSHOT (used e.g. by diagnostics)
    # ============================================================
    def get_state_snapshot(self) -> Dict[str, Any]:
        # Diagnostics: emotion is per-conversation in MoodState; this snapshot
        # reports the legacy (no-conversation) default.
        emotion_str, emotion_intensity = self._derive_emotion(None)
        return {
            "core_version": self.core_version,
            "emotion": emotion_str,
            "emotion_intensity": emotion_intensity,
            "creator_affinity": self.bond_engine.affinity,
            "llm_mode": getattr(self, "llm_mode", "online"),
            "local_model": getattr(self, "local_model_name", None),
            "memory_enabled": self.memory_enabled,
        }

    # ============================================================
    # MEMORY SYSTEM HELPERS
    # ============================================================
    def get_memory_debug_info(self, conversation_id: int) -> Dict[str, Any]:
        """
        Get debug information about memory state for a conversation.
        For internal debugging only - never expose to user.
        """
        if not self.memory_enabled or not self._memory_store:
            return {"memory_enabled": False}

        try:
            state = self._memory_store.get_task_state(conversation_id)
            rolling = self._memory_store.get_rolling_summary(conversation_id)
            chunks = self._memory_store.get_chunk_summaries(conversation_id)
            msg_count = self._memory_store.get_message_count(conversation_id)

            return {
                "memory_enabled": True,
                "conversation_id": conversation_id,
                "message_count": msg_count,
                "task_state": state.to_dict(),
                "rolling_summary": rolling.to_dict(),
                "chunk_count": len(chunks),
                "chunks": [c.to_dict() for c in chunks],
            }
        except Exception as e:
            return {"memory_enabled": True, "error": str(e)}

    def clear_conversation_memory(self, conversation_id: int):
        """
        Clear all memory state for a conversation.
        Useful for starting fresh or testing.
        """
        if not self.memory_enabled or not self._memory_store:
            return

        try:
            self._memory_store.clear_task_state(conversation_id)
            # Note: Rolling summary and chunks are tied to conversation
            # and will be rebuilt as needed
            print(f"[SARAH] Cleared memory for conversation {conversation_id}")
        except Exception as e:
            print(f"[SARAH] Failed to clear memory: {e}")
