# backend/ai_/intent_router.py
from dataclasses import dataclass
from typing import Literal, Dict, Any


IntentType = Literal[
    "chat",
    "coding",
    "memory_query",
    "memory_update",
    "tool_use",
    "task_create",
    "task_query",
    "system",
]


@dataclass
class Intent:
    type: IntentType
    confidence: float
    raw_reasoning: str


class IntentRouter:
    """
    Very lightweight, LLM-driven intent classifier.
    You give it (user_message, context), it returns a simple Intent object.
    """

    def __init__(self, llm_call_fn):
        """
        llm_call_fn(prompt: str, **kwargs) -> str
        This should be a small wrapper SarahCore already has around your main LLM.
        """
        self.llm_call_fn = llm_call_fn

    async def classify(self, user_message: str, context_summary: str = "") -> Intent:
        prompt = f"""
You are an intent classifier inside a local assistant called Sarah.

User message:
\"\"\"{user_message}\"\"\"

Context summary:
\"\"\"{context_summary}\"\"\"

Classify the MAIN intent of the user into exactly one of these:
- chat
- coding
- memory_query
- memory_update
- tool_use
- task_create
- task_query
- system

Reply in JSON only, no prose. Use keys:
- type: one of the labels
- confidence: 0.0 to 1.0
- reasoning: short explanation
"""
        import json

        raw = await self.llm_call_fn(prompt, max_tokens=120)
        try:
            data = json.loads(raw)
            t = data.get("type", "chat")
            conf = float(data.get("confidence", 0.6))
            reason = data.get("reasoning", "")
            if t not in [
                "chat",
                "coding",
                "memory_query",
                "memory_update",
                "tool_use",
                "task_create",
                "task_query",
                "system",
            ]:
                t = "chat"
            return Intent(type=t, confidence=conf, raw_reasoning=reason)
        except Exception:
            # Fallback to chat if parsing fails
            return Intent(type="chat", confidence=0.3, raw_reasoning=raw[:200])
