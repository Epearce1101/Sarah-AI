# backend/ai_v11/multi_agent.py
from typing import Dict, Any, Optional, List
from dataclasses import dataclass

from .intent_router import IntentRouter, Intent
from .tasks_engine import TaskEngine
from .reflection_engine import ReflectionEngine


@dataclass
class AgentResponse:
    reply: str
    intent: Intent
    created_task_id: Optional[int] = None
    meta: Dict[str, Any] = None


class MultiAgentBrain:
    """
    Hybrid-Mode V10 Multi-Agent Brain
    ---------------------------------
    Behaviors:
      - Uses intent classifier to understand message
      - Uses task engine for actionable tasks
      - Uses reflection engine ONLY when needed
      - Hybrid mode: Sarah avoids unnecessary meta-talk
        unless the message explicitly triggers reflection
    """

    # Reflection keywords (Hybrid Mode trigger)
    REFLECTION_KEYWORDS = [
        "improve yourself",
        "self improvement",
        "self-improvement",
        "change your behavior",
        "update your behavior",
        "update your rules",
        "adjust your rules",
        "modify your personality",
        "creator feedback",
        "improve your memory",
        "modify your memory",
        "self review",
        "self-review",
        "reflect on",
    ]

    def __init__(
        self,
        llm_call_fn,
        intent_router: IntentRouter,
        task_engine: TaskEngine,
        reflection_engine: ReflectionEngine,
    ):
        self.llm_call_fn = llm_call_fn
        self.intent_router = intent_router
        self.task_engine = task_engine
        self.reflection_engine = reflection_engine

    # --------------------------------------------------------
    # HYBRID MODE REFLECTION CHECK
    # --------------------------------------------------------
    def _should_reflect(self, message: str) -> bool:
        """
        Reflection triggers ONLY when:
          - Creator gives meta-feedback
          - Sarah's rules or personality are modified
          - Self-improvement instructions are given
          - Long-term memory update instructions
        """
        m = message.lower()
        return any(key in m for key in self.REFLECTION_KEYWORDS)

    # --------------------------------------------------------
    # MAIN MESSAGE HANDLER (HYBRID MODE)
    # --------------------------------------------------------
    async def handle_message(
        self,
        user_message: str,
        conversation_summary: str = "",
        conversation_id: Optional[int] = None,
    ) -> AgentResponse:

        # 1) Classify intent normally
        intent = await self.intent_router.classify(
            user_message=user_message,
            context_summary=conversation_summary,
        )

        # ----------------------------------------------------
        # 2) Hybrid Mode Logic:
        #    Only reflect when needed. Otherwise skip.
        # ----------------------------------------------------
        if self._should_reflect(user_message):
            short_reflection = await self.reflection_engine.generate_short_reflection(
                user_message=user_message,
                context_summary=conversation_summary,
            )

            return AgentResponse(
                reply=short_reflection,
                intent=intent,
                created_task_id=None,
                meta={"reflection_triggered": True},
            )

        # ----------------------------------------------------
        # 3) Build useful context (tasks + self-improvement notes)
        # ----------------------------------------------------
        task_summary = self.task_engine.summarize_tasks_for_prompt()
        reflection_summary = self.reflection_engine.build_reflection_summary()

        context_parts: List[str] = []
        if task_summary:
            context_parts.append("Active tasks:\n" + task_summary)
        if reflection_summary:
            context_parts.append("Self-improvement notes:\n" + reflection_summary)

        context_block = "\n\n".join(context_parts).strip()

        # ----------------------------------------------------
        # 4) Produce natural reply (no meta-talk)
        # ----------------------------------------------------
        system_hint = f"""
You are the Multi-Agent Brain for Sarah AI running in HYBRID MODE.

Intent classification result:
- type = {intent.type}
- confidence = {intent.confidence}

Rules for HYBRID MODE:
- DO NOT produce long meta explanations.
- DO NOT mention reviewing conversation history.
- DO NOT mention Creator feedback unless explicitly asked.
- If the request is simple or code-related, DO IT immediately.
- Keep replies efficient, focused, and friendly.
"""

        full_prompt = f"{system_hint}\n"
        if context_block:
            full_prompt += f"\nContext:\n{context_block}\n"
        full_prompt += f"\nUser says:\n\"\"\"{user_message}\"\"\"\n"
        full_prompt += "\nRespond naturally as Sarah.\n"

        reply = await self.llm_call_fn(full_prompt, max_tokens=600)

        # ----------------------------------------------------
        # 5) Automatic Task Creation
        # ----------------------------------------------------
        created_task_id = None
        meta: Dict[str, Any] = {}

        if intent.type == "task_create" and intent.confidence >= 0.6:
            import json

            task_prompt = f"""
User message:
\"\"\"{user_message}\"\"\"

Intent=task_create.
Extract JSON:
- title
- description
- priority (0-3)

Reply JSON only.
"""
            raw = await self.llm_call_fn(task_prompt, max_tokens=200)

            try:
                data = json.loads(raw)
                title = (data.get("title") or "Untitled task").strip()
                description = (data.get("description") or "").strip()
                priority = int(data.get("priority") or 0)

                created_task_id = self.task_engine.create_task(
                    title=title,
                    description=description,
                    priority=priority,
                )

                meta["task_created"] = {
                    "id": created_task_id,
                    "title": title,
                    "priority": priority,
                }

            except Exception:
                pass

        # ----------------------------------------------------
        # 6) Final Agent Response
        # ----------------------------------------------------
        return AgentResponse(
            reply=reply,
            intent=intent,
            created_task_id=created_task_id,
            meta=meta,
        )
