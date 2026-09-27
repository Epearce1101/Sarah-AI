# backend/memory/summarizer.py
"""
Summarizer
==========
Generates rolling summaries and chunk summaries for conversation memory.

All summaries are SILENT - for internal context building only, never shown to user.
"""

import asyncio
import json
import re
from typing import Optional, List, Dict, Any, Callable, Awaitable

from .config import MemoryConfig, get_memory_config
from .memory_store import (
    MemoryStore,
    get_memory_store,
    RollingSummary,
    TaskState,
)


class Summarizer:
    """
    Generates and manages conversation summaries.

    - Chunk summaries: 2-6 bullets every N messages
    - Rolling summary: Updated every N turns with goal/decisions/progress/constraints/next
    """

    # Prompt for chunk summarization
    CHUNK_SUMMARY_PROMPT = """Summarize these conversation messages in 2-6 bullet points.
Focus on: key decisions, important information shared, actions taken or planned.
Keep each bullet under 20 words. Output only the bullets, no preamble.

Messages:
{messages}

Bullets:"""

    # Prompt for rolling summary update
    ROLLING_SUMMARY_PROMPT = """Update the conversation summary based on new messages.

Current summary:
{current_summary}

New messages since last update:
{new_messages}

Extract and update in this JSON format:
{{
    "goal": "What the user is trying to achieve (1 sentence)",
    "decisions": ["Decision 1", "Decision 2"],
    "progress": "What has been accomplished so far (1-2 sentences)",
    "constraints": ["Constraint 1", "Constraint 2"],
    "next_step": "What needs to happen next (1 sentence)"
}}

Only include non-empty fields. Output valid JSON only:"""

    # Prompt for extracting task state from assistant response
    TASK_STATE_PROMPT = """Analyze this assistant response and extract task state.

Response:
{response}

Previous state:
{previous_state}

If the response:
- Asks a question, set pending_question
- Offers numbered choices, set pending_choices as a list
- Mentions what to do next, set next_step
- Completes a task, update current_task

Output JSON:
{{
    "pending_question": "The question asked, if any",
    "pending_choices": ["Choice 1", "Choice 2"],
    "next_step": "Next action mentioned",
    "current_task": "Current task being worked on",
    "last_assistant_action": "Brief summary of what assistant just did"
}}

Output valid JSON only (empty strings for missing fields):"""

    def __init__(
        self,
        llm_call_fn: Callable[[str, int], Awaitable[str]],
        store: Optional[MemoryStore] = None,
        config: Optional[MemoryConfig] = None,
    ):
        """
        Args:
            llm_call_fn: Async function (prompt, max_tokens) -> response
            store: MemoryStore instance
            config: MemoryConfig instance
        """
        self.llm_call_fn = llm_call_fn
        self.store = store or get_memory_store()
        self.config = config or get_memory_config()
        self._update_locks: Dict[int, asyncio.Lock] = {}

        if self.config.debug_memory:
            print("[Summarizer] Initialized")

    # ============================================================
    # CHUNK SUMMARIES
    # ============================================================

    def _format_messages_for_summary(self, messages: List[Dict[str, Any]]) -> str:
        """Format messages for summarization prompt."""
        lines = []
        for msg in messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            # Truncate very long messages
            if len(content) > 500:
                content = content[:500] + "..."
            lines.append(f"[{role}]: {content}")
        return "\n".join(lines)

    async def should_create_chunk(self, conversation_id: int) -> bool:
        """Check if we should create a new chunk summary."""
        unchunked = self.store.get_unchunked_messages(conversation_id)
        return len(unchunked) >= self.config.chunk_size

    async def create_chunk_summary(self, conversation_id: int) -> Optional[int]:
        """
        Create a chunk summary for unchunked messages.
        Returns the chunk ID if created, None otherwise.
        """
        unchunked = self.store.get_unchunked_messages(conversation_id)

        if len(unchunked) < self.config.chunk_size:
            return None

        # Take exactly chunk_size messages
        chunk_messages = unchunked[:self.config.chunk_size]

        if not chunk_messages:
            return None

        start_id = chunk_messages[0]["id"]
        end_id = chunk_messages[-1]["id"]

        # Generate summary
        messages_text = self._format_messages_for_summary(chunk_messages)
        prompt = self.CHUNK_SUMMARY_PROMPT.format(messages=messages_text)

        try:
            summary = await self.llm_call_fn(prompt, self.config.chunk_summary_tokens)
            summary = summary.strip()

            if not summary:
                summary = "- Conversation continued"

            # Store the chunk
            chunk_id = self.store.add_chunk_summary(
                conversation_id=conversation_id,
                start_message_id=start_id,
                end_message_id=end_id,
                summary=summary,
            )

            # Cleanup old chunks if needed
            self.store.delete_old_chunks(
                conversation_id,
                keep_count=self.config.max_chunks_in_context + 2  # Keep a few extra
            )

            if self.config.debug_memory:
                print(f"[Summarizer] Created chunk {chunk_id}: {summary[:100]}...")

            return chunk_id

        except Exception as e:
            print(f"[Summarizer] Chunk summary error: {e}")
            return None

    async def create_chunks_if_needed(self, conversation_id: int) -> List[int]:
        """
        Create multiple chunk summaries if needed.
        Returns list of created chunk IDs.
        """
        created = []
        while await self.should_create_chunk(conversation_id):
            chunk_id = await self.create_chunk_summary(conversation_id)
            if chunk_id:
                created.append(chunk_id)
            else:
                break
        return created

    # ============================================================
    # ROLLING SUMMARY
    # ============================================================

    async def should_update_rolling_summary(self, conversation_id: int) -> bool:
        """Check if rolling summary needs update."""
        current = self.store.get_rolling_summary(conversation_id)
        total_messages = self.store.get_message_count(conversation_id)

        # Update if enough new messages since last update
        messages_since_update = total_messages - current.message_count
        return messages_since_update >= (self.config.rolling_update_interval * 2)  # *2 for user+assistant

    async def update_rolling_summary(
        self,
        conversation_id: int,
        force: bool = False,
    ) -> Optional[RollingSummary]:
        """
        Update the rolling summary with recent messages.
        Returns the updated summary, or None if not updated.
        """
        if not force and not await self.should_update_rolling_summary(conversation_id):
            return None

        current = self.store.get_rolling_summary(conversation_id)
        total_messages = self.store.get_message_count(conversation_id)

        # Get messages since last update
        recent = self.store.get_recent_messages(
            conversation_id,
            limit=self.config.rolling_update_interval * 2 + 10  # A bit more for context
        )

        if not recent:
            return None

        # Format current summary
        current_summary_text = current.to_prompt_block() or "No summary yet."
        new_messages_text = self._format_messages_for_summary(recent)

        prompt = self.ROLLING_SUMMARY_PROMPT.format(
            current_summary=current_summary_text,
            new_messages=new_messages_text,
        )

        try:
            response = await self.llm_call_fn(prompt, self.config.rolling_summary_tokens)
            response = response.strip()

            # Parse JSON response
            # Try to extract JSON if wrapped in markdown
            json_match = re.search(r'\{[\s\S]*\}', response)
            if json_match:
                response = json_match.group(0)

            data = json.loads(response)

            updated = RollingSummary(
                goal=data.get("goal", current.goal) or current.goal,
                decisions=data.get("decisions", current.decisions) or current.decisions,
                progress=data.get("progress", current.progress) or current.progress,
                constraints=data.get("constraints", current.constraints) or current.constraints,
                next_step=data.get("next_step", current.next_step) or current.next_step,
                message_count=total_messages,
            )

            self.store.save_rolling_summary(conversation_id, updated)

            if self.config.debug_memory:
                print(f"[Summarizer] Updated rolling summary: goal={updated.goal[:50] if updated.goal else 'none'}...")

            return updated

        except json.JSONDecodeError as e:
            print(f"[Summarizer] Rolling summary JSON parse error: {e}")
            return None
        except Exception as e:
            print(f"[Summarizer] Rolling summary error: {e}")
            return None

    # ============================================================
    # TASK STATE EXTRACTION
    # ============================================================

    async def extract_task_state_from_response(
        self,
        conversation_id: int,
        assistant_response: str,
    ) -> TaskState:
        """
        Extract task state from assistant's response.
        Updates pending_question, pending_choices, next_step, etc.
        """
        current = self.store.get_task_state(conversation_id)

        # Format previous state for prompt
        prev_state_text = f"""
goal: {current.goal}
current_task: {current.current_task}
next_step: {current.next_step}
pending_question: {current.pending_question}
pending_choices: {current.pending_choices}
""".strip()

        prompt = self.TASK_STATE_PROMPT.format(
            response=assistant_response[:1500],  # Truncate if very long
            previous_state=prev_state_text,
        )

        try:
            response = await self.llm_call_fn(prompt, 300)
            response = response.strip()

            # Extract JSON
            json_match = re.search(r'\{[\s\S]*\}', response)
            if json_match:
                response = json_match.group(0)

            data = json.loads(response)

            # Update state with extracted values
            updated = self.store.update_task_state(
                conversation_id=conversation_id,
                pending_question=data.get("pending_question") or "",
                pending_choices=data.get("pending_choices") or [],
                next_step=data.get("next_step") or current.next_step,
                current_task=data.get("current_task") or current.current_task,
                last_assistant_action=data.get("last_assistant_action") or "",
                increment_turn=True,
            )

            if self.config.debug_memory:
                print(f"[Summarizer] Extracted task state: q={updated.pending_question[:30] if updated.pending_question else 'none'}...")

            return updated

        except json.JSONDecodeError:
            # Just increment turn count if extraction fails
            return self.store.update_task_state(
                conversation_id=conversation_id,
                increment_turn=True,
            )
        except Exception as e:
            print(f"[Summarizer] Task state extraction error: {e}")
            return current

    async def set_goal_from_message(
        self,
        conversation_id: int,
        user_message: str,
    ) -> TaskState:
        """
        Set the conversation goal from a user message.
        Called when no goal is set or when explicitly starting new task.
        """
        current = self.store.get_task_state(conversation_id)

        if not current.goal or len(user_message) > 50:
            # Extract goal (simplified - could use LLM for complex extraction)
            goal = user_message
            if len(goal) > 200:
                goal = goal[:200] + "..."

            return self.store.update_task_state(
                conversation_id=conversation_id,
                goal=goal,
            )

        return current

    # ============================================================
    # COMBINED UPDATE
    # ============================================================

    async def update_all(
        self,
        conversation_id: int,
        assistant_response: Optional[str] = None,
    ):
        """
        Run all summary updates after a conversation turn.
        Call this after saving the assistant's response to the database.

        Serialized per conversation: back-to-back turns otherwise both see the
        same unchunked range and write duplicate chunk summaries.
        """
        lock = self._update_locks.setdefault(conversation_id, asyncio.Lock())
        async with lock:
            # Create chunks if needed
            await self.create_chunks_if_needed(conversation_id)

            # Update rolling summary if needed
            await self.update_rolling_summary(conversation_id)

            # Extract task state from response
            if assistant_response:
                await self.extract_task_state_from_response(
                    conversation_id,
                    assistant_response,
                )
