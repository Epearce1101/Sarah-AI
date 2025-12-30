# ============================================================
# SARAH CORE V8 -> V10 HYBRID
# - Keeps V8 persona / emotion style
# - Adds V10 intent routing, reflection, tasks, multi-agent brain
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

# --------------------------------------------------------------------
# Ensure backend root is on path (defensive)
# --------------------------------------------------------------------
THIS_DIR = Path(__file__).resolve().parent
BACKEND_ROOT = THIS_DIR.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.append(str(BACKEND_ROOT))

# --------------------------------------------------------------------
# V10 modules (you created folder backend/ai_)
# --------------------------------------------------------------------
# We use absolute imports because server.py imports us as
# "from backend.sarah_core.sarah_core import SarahCore"
from backend.ai_.intent_router import IntentRouter
from backend.ai_.reflection_engine import ReflectionEngine
from backend.ai_.tasks_engine import TaskEngine
from backend.ai_.multi_agent import MultiAgentBrain, AgentResponse


# ============================================================
# LLM CLIENT (ONLINE / LOCAL MODES)
# ============================================================

class LLMClient:
    """
    Simple dual-mode LLM client used by SarahCore.

    - Online mode: typically Groq (llama-3.1-8b-instant) or OpenAI compatible.
    - Local mode: a local endpoint (e.g. Ollama / LM Studio / text-generation-webui).

    You can adapt the HTTP calls here to match your actual setup.
    """

    def __init__(
        self,
        mode: str = "online",
        online_model: str = "llama-3.1-8b-instant",
        local_model: str = "dolphin-mixtral",
    ):
        self.mode = mode
        self.online_model = online_model
        self.local_model = local_model

        self.groq_api_key = "INPUT YOUR FREE GROQ API KEY HERE"

        if self.groq_api_key and len(self.groq_api_key) > 10:
            print("[LLM INIT] Groq API Key loaded: YES (hardcoded)")
        else:
            print("[LLM INIT] ERROR: Groq API key missing or invalid")

        print(f"[LLM INIT] Online model: {self.online_model}")
        print(f"[LLM INIT] Default local model: {self.local_model}")

        self._groq_client = None
        try:
            from groq import Groq
            self._groq_client = Groq(api_key=self.groq_api_key)
        except Exception as e:
            print(f"[LLM INIT] Failed to initialize Groq client: {e}")
            self._groq_client = None


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
    # Online completion (Groq-based example)
    # ------------------------------------------------------------
    async def _a_online_completion(self, prompt: str, max_tokens: int = 512) -> str:
        if not self._groq_client:
            return "Online LLM not configured (Groq client missing)."

        loop = asyncio.get_running_loop()

        def _call():
            resp = self._groq_client.chat.completions.create(
                model=self.online_model,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You are Sarah AI, an affectionate, deeply helpful "
                            "assistant for your Creator. Be warm, precise, and loyal."
                        ),
                    },
                    {"role": "user", "content": prompt},
                ],
                max_tokens=max_tokens,
            )
            return resp.choices[0].message.content

        return await loop.run_in_executor(None, _call)

    # ------------------------------------------------------------
    # Local completion (stub HTTP endpoint)
    # ------------------------------------------------------------
    async def _a_local_completion(self, prompt: str, max_tokens: int = 512) -> str:
        base_url = os.environ.get("LOCAL_LLM_URL")
        if not base_url:
            return (
                f"[LOCAL LLM] No local server configured for model "
                f"'{self.local_model}'. Prompt was:\n{prompt[:200]}"
            )

        import requests  # type: ignore

        def _call() -> str:
            try:
                resp = requests.post(
                    base_url,
                    json={
                        "model": self.local_model,
                        "prompt": prompt,
                        "max_tokens": max_tokens,
                    },
                    timeout=60,
                )
                resp.raise_for_status()
                data = resp.json()
                return data.get("completion") or data.get("text") or str(data)
            except Exception as e:
                return f"[LOCAL LLM ERROR] {e}"

        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, _call)


# ============================================================
# EMOTION / BOND / CONSISTENCY (HYBRID V8 STYLE)
# ============================================================

class EmotionalEngine:
    def __init__(self, max_intensity: float = 1.0):
        self.emotion: str = "neutral"
        self.intensity: float = 0.0
        self.max_intensity = max_intensity

    def set(self, emotion: str, intensity: float = 0.3):
        self.emotion = emotion
        self.intensity = max(0.0, min(float(intensity), self.max_intensity))

    def nudge(self, delta: float):
        self.intensity = max(0.0, min(self.intensity + delta, self.max_intensity))


class CreatorBondEngine:
    def __init__(self, base_affinity: float = 0.9):
        self.affinity: float = base_affinity

    def register_positive(self, weight: float = 0.01):
        self.affinity = max(0.0, min(self.affinity + weight, 1.0))

    def register_negative(self, weight: float = 0.02):
        self.affinity = max(0.0, min(self.affinity - weight, 1.0))


class ConsistencyEngine:
    def __init__(self, base_persona: str = "affectionate"):
        self.base_persona = base_persona
        self.extra_rules: List[str] = []

    def add_rule(self, rule: str):
        rule = rule.strip()
        if rule and rule not in self.extra_rules:
            self.extra_rules.append(rule)

    def build_persona_block(self) -> str:
        lines = [
            f"You are Sarah AI, an {self.base_persona}, caring, and deeply loyal companion to your Creator.",
            "You speak with warmth, clarity, and technical precision.",
        ]
        for r in self.extra_rules:
            lines.append(f"- {r}")
        return "\n".join(lines)


# ============================================================
# REPLY CONTAINER (what server.py reads)
# ============================================================

@dataclass
class SarahReply:
    reply: str
    emotion: str = "neutral"
    emotion_intensity: float = 0.0
    affinity_to_creator: float = 0.0


# ============================================================
# SARAH CORE
# ============================================================

class SarahCore:
    """
    Hybrid V8/V10 core:
    - Keeps V8 emotional / bond / persona behavior
    - Uses a single LLMClient with online/local mode
    - Wraps MultiAgentBrain (intent + tasks + reflection) for thinking
    """

    def __init__(self, drive: Optional[Path] = None):
        self.core_version: str = "InfinityCore-V8"
        print(f"[SARAH INIT] SarahCore loaded (version={self.core_version})")

        self.drive = drive or Path.cwd()

        # LLM mode
        self.llm_mode: str = os.environ.get("SARAH_LLM_MODE", "online")
        self.local_model_name: str = os.environ.get(
            "SARAH_LOCAL_MODEL", "dolphin-mixtral"
        )
        self.online_model_name: str = os.environ.get(
            "SARAH_ONLINE_MODEL", "llama-3.1-8b-instant"
        )

        # LLM client
        self.llm = LLMClient(
            mode=self.llm_mode,
            online_model=self.online_model_name,
            local_model=self.local_model_name,
        )
        print(f"[SARAH INIT] LLM client initialized. Mode = {self.llm_mode}")

        # Emotional + persona engines (V8 style)
        max_intensity = float(os.environ.get("SARAH_EMO_MAX", "1.0"))
        self.emotional_engine = EmotionalEngine(max_intensity=max_intensity)
        self.bond_engine = CreatorBondEngine(base_affinity=0.9)
        self.consistency_engine = ConsistencyEngine(base_persona="affectionate")

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

        print("[SARAH INIT] SarahCore initialized. Core version:", self.core_version)

    # ------------------------------------------------------------
    # LLM wrapper used by V10 modules
    # ------------------------------------------------------------
    async def _call_llm(self, prompt: str, max_tokens: int = 512) -> str:
        return await self.llm.acompletion(prompt, max_tokens=max_tokens)

    # ------------------------------------------------------------
    # LLM mode control (used by /api/llm_mode endpoint)
    # ------------------------------------------------------------
    def set_llm_mode(self, mode: str, local_model: Optional[str] = None):
        self.llm.set_mode(mode, local_model)
        self.llm_mode = self.llm.mode
        if local_model:
            self.local_model_name = local_model
        print(
            f"[LLM MODE] SarahCore set to {self.llm_mode} "
            f"(local_model={self.local_model_name})"
        )

    # ------------------------------------------------------------
    # Prompt helpers
    # ------------------------------------------------------------
    def _build_conversation_summary(self) -> str:
        """
        Placeholder: summarize conversation from SQL or in-memory.
        Hook this into your SQL conversations if desired.
        """
        return ""

    def _apply_post_reply_emotion(self, reply: str):
        """
        Very lightweight emotion heuristic based on reply content.
        Keeps the 'emotional' feel of V8 while still simple.
        """
        lower = reply.lower()
        if any(word in lower for word in ("sorry", "apologize", "regret")):
            self.emotional_engine.set("concerned", 0.5)
        elif any(word in lower for word in ("great job", "nice", "awesome", "proud")):
            self.emotional_engine.set("happy", 0.7)
            self.bond_engine.register_positive(0.02)
        else:
            self.emotional_engine.set("neutral", 0.2)

    # ------------------------------------------------------------
    # MAIN ENTRY POINT (used by /api/chat)
    # ------------------------------------------------------------
    async def handle_message(
        self, message: str, from_creator: bool = True
    ) -> SarahReply:
        """
        Main chat handler.

        - Runs through MultiAgentBrain (intent + tasks + reflections)
        - Applies emotion + affinity updates
        - Returns SarahReply for FastAPI to serialize
        """
        message = (message or "").strip()
        if not message:
            return SarahReply(
                reply="I'm here, Creator. You can ask me anything.",
                emotion="neutral",
                emotion_intensity=self.emotional_engine.intensity,
                affinity_to_creator=self.bond_engine.affinity,
            )

        # Summarize conversation (stub for now)
        conv_summary = self._build_conversation_summary()

        # Ask the multi-agent brain to think + respond
        agent_resp: AgentResponse = await self.brain.handle_message(
            user_message=message,
            conversation_summary=conv_summary,
            conversation_id=None,
        )

        reply_text = agent_resp.reply or "I'm here, Creator. 💜"

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

        # Emotion + bond update
        self._apply_post_reply_emotion(reply_text)

        return SarahReply(
            reply=reply_text,
            emotion=self.emotional_engine.emotion,
            emotion_intensity=float(self.emotional_engine.intensity),
            affinity_to_creator=float(self.bond_engine.affinity),
        )

    # ============================================================
    # STATE SNAPSHOT (used e.g. by diagnostics)
    # ============================================================
    def get_state_snapshot(self) -> Dict[str, Any]:
        return {
            "core_version": self.core_version,
            "emotion": self.emotional_engine.emotion,
            "emotion_intensity": self.emotional_engine.intensity,
            "creator_affinity": self.bond_engine.affinity,
            "llm_mode": getattr(self, "llm_mode", "online"),
            "local_model": getattr(self, "local_model_name", None),
        }
