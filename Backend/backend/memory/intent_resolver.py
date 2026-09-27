# backend/memory/intent_resolver.py
"""
Intent Resolver
===============
Handles short replies and implicit user intents using task state context.

When user says "yes", "option 2", or "sounds good", we need to understand
what they're referring to based on conversation state.

This is SILENT - resolution happens internally, never shown to user.
"""

import re
from dataclasses import dataclass
from typing import Optional, List, Dict, Any
from enum import Enum

from .config import MemoryConfig, get_memory_config
from .memory_store import MemoryStore, get_memory_store, TaskState


class IntentType(Enum):
    """Types of resolved intents from short replies."""
    CONFIRMATION = "confirmation"      # yes, okay, sure
    NEGATION = "negation"              # no, cancel, stop
    SELECTION = "selection"            # option 1, the first one, b
    CONTINUATION = "continuation"      # continue, go on, next
    QUESTION = "question"              # short question
    UNCLEAR = "unclear"                # couldn't resolve
    NORMAL = "normal"                  # not a short reply, process normally


@dataclass
class ResolvedIntent:
    """Result of intent resolution."""
    type: IntentType
    original_message: str
    expanded_message: str              # What the user likely meant
    confidence: float                  # 0.0 - 1.0
    selected_choice: Optional[str] = None  # If selection, which one
    selected_index: Optional[int] = None   # Index in choices (0-based)
    context_used: Dict[str, Any] = None    # Debug info about resolution

    def __post_init__(self):
        if self.context_used is None:
            self.context_used = {}


class IntentResolver:
    """
    Resolves short/implicit user replies to explicit intents.

    Uses task state (pending_question, pending_choices) to expand:
    - "yes" -> "Yes, I want to proceed with [pending action]"
    - "2" -> "I choose [choice 2 from pending_choices]"
    - "ok" -> "Okay, continue with [next_step]"
    """

    # Number words mapping
    NUMBER_WORDS = {
        "first": 0, "one": 0, "1": 0, "a": 0,
        "second": 1, "two": 1, "2": 1, "b": 1,
        "third": 2, "three": 2, "3": 2, "c": 2,
        "fourth": 3, "four": 3, "4": 3, "d": 3,
        "fifth": 4, "five": 4, "5": 4, "e": 4,
        "sixth": 5, "six": 5, "6": 5,
        "seventh": 6, "seven": 6, "7": 6,
        "eighth": 7, "eight": 7, "8": 7,
        "ninth": 8, "nine": 8, "9": 8,
    }

    def __init__(
        self,
        store: Optional[MemoryStore] = None,
        config: Optional[MemoryConfig] = None,
    ):
        self.store = store or get_memory_store()
        self.config = config or get_memory_config()

        # Compile patterns
        self._selection_patterns = [
            re.compile(p, re.IGNORECASE)
            for p in self.config.selection_patterns
        ]

        if self.config.debug_memory:
            print("[IntentResolver] Initialized")

    def _is_short_reply(self, message: str) -> bool:
        """Check if message is a short reply."""
        words = message.split()
        return len(words) <= self.config.short_reply_word_limit

    def _is_confirmation(self, message: str) -> bool:
        """Check if message is a confirmation."""
        normalized = message.lower().strip().rstrip("!.,?")
        return normalized in self.config.confirmation_keywords

    def _is_negation(self, message: str) -> bool:
        """Check if message is a negation."""
        normalized = message.lower().strip().rstrip("!.,?")
        return normalized in self.config.negation_keywords

    def _extract_selection(self, message: str) -> Optional[int]:
        """
        Extract selection index from message.
        Returns 0-based index, or None if not a selection.
        """
        normalized = message.lower().strip().rstrip("!.,?")

        # Check compiled patterns
        for pattern in self._selection_patterns:
            if pattern.match(normalized):
                # Extract the number/letter
                for word in normalized.split():
                    word = word.strip()
                    if word in self.NUMBER_WORDS:
                        return self.NUMBER_WORDS[word]

        # Direct number check
        if normalized.isdigit():
            num = int(normalized)
            if 1 <= num <= 9:
                return num - 1  # Convert to 0-based

        # Direct letter check
        if len(normalized) == 1 and normalized.isalpha():
            idx = ord(normalized) - ord('a')
            if 0 <= idx <= 8:
                return idx

        # Check for number words in message
        words = normalized.split()
        for word in words:
            if word in self.NUMBER_WORDS:
                return self.NUMBER_WORDS[word]

        return None

    def _is_continuation(self, message: str) -> bool:
        """Check if message is a continuation request."""
        normalized = message.lower().strip().rstrip("!.,?")
        continuation_phrases = [
            "continue", "go on", "proceed", "next", "keep going",
            "go ahead", "carry on", "move on", "what's next",
            "then what", "and then", "now what"
        ]
        return normalized in continuation_phrases

    def _is_question(self, message: str) -> bool:
        """Check if message is a question."""
        return message.strip().endswith("?")

    def resolve(
        self,
        message: str,
        conversation_id: int,
    ) -> ResolvedIntent:
        """
        Resolve a user message to an explicit intent.

        Args:
            message: User's message
            conversation_id: Current conversation ID

        Returns:
            ResolvedIntent with type, expanded message, and confidence
        """
        original = message.strip()

        # Not a short reply - process normally
        if not self._is_short_reply(original):
            return ResolvedIntent(
                type=IntentType.NORMAL,
                original_message=original,
                expanded_message=original,
                confidence=1.0,
            )

        # Get current task state
        state = self.store.get_task_state(conversation_id)
        context = {
            "has_pending_question": state.has_pending_question(),
            "has_pending_choices": state.has_pending_choices(),
            "pending_question": state.pending_question,
            "pending_choices": state.pending_choices,
            "next_step": state.next_step,
            "last_assistant_action": state.last_assistant_action,
        }

        # Check for selection (numbered choice)
        selection_idx = self._extract_selection(original)
        if selection_idx is not None and state.has_pending_choices():
            if selection_idx < len(state.pending_choices):
                selected = state.pending_choices[selection_idx]
                expanded = f"I choose: {selected}"

                # Clear pending after selection
                self.store.update_task_state(
                    conversation_id,
                    clear_pending=True,
                )

                return ResolvedIntent(
                    type=IntentType.SELECTION,
                    original_message=original,
                    expanded_message=expanded,
                    confidence=0.95,
                    selected_choice=selected,
                    selected_index=selection_idx,
                    context_used=context,
                )

        # Continuation phrases like "continue" also appear in the broad
        # confirmation vocabulary, so classify them before confirmations.
        if self._is_continuation(original):
            if state.next_step:
                expanded = f"Continue with: {state.next_step}"
                confidence = 0.85
            else:
                expanded = "Continue with the current task."
                confidence = 0.6

            return ResolvedIntent(
                type=IntentType.CONTINUATION,
                original_message=original,
                expanded_message=expanded,
                confidence=confidence,
                context_used=context,
            )

        # Check for confirmation
        if self._is_confirmation(original):
            if state.has_pending_question():
                expanded = f"Yes. (Regarding: {state.pending_question})"
                confidence = 0.9
            elif state.next_step:
                expanded = f"Yes, proceed with: {state.next_step}"
                confidence = 0.85
            elif state.last_assistant_action:
                expanded = f"Yes. (Confirming: {state.last_assistant_action})"
                confidence = 0.8
            else:
                expanded = "Yes, proceed."
                confidence = 0.7

            # Clear pending after confirmation
            self.store.update_task_state(
                conversation_id,
                clear_pending=True,
            )

            return ResolvedIntent(
                type=IntentType.CONFIRMATION,
                original_message=original,
                expanded_message=expanded,
                confidence=confidence,
                context_used=context,
            )

        # Check for negation
        if self._is_negation(original):
            if state.has_pending_question():
                expanded = f"No. (Regarding: {state.pending_question})"
                confidence = 0.9
            elif state.has_pending_choices():
                expanded = "No, I don't want any of those options."
                confidence = 0.85
            else:
                expanded = "No, stop."
                confidence = 0.7

            # Clear pending after negation
            self.store.update_task_state(
                conversation_id,
                clear_pending=True,
            )

            return ResolvedIntent(
                type=IntentType.NEGATION,
                original_message=original,
                expanded_message=expanded,
                confidence=confidence,
                context_used=context,
            )

        # Short question - pass through but note it
        if self._is_question(original):
            return ResolvedIntent(
                type=IntentType.QUESTION,
                original_message=original,
                expanded_message=original,
                confidence=0.8,
                context_used=context,
            )

        # Short but substantive requests should pass through unchanged when no
        # task context exists; otherwise normal user commands can be mislabeled
        # as ambiguous just because they are under the short-reply word limit.
        has_context = (
            state.has_pending_question()
            or state.has_pending_choices()
            or bool(state.next_step.strip())
            or bool(state.last_assistant_action.strip())
        )
        if not has_context and len(original.split()) > 3:
            return ResolvedIntent(
                type=IntentType.NORMAL,
                original_message=original,
                expanded_message=original,
                confidence=1.0,
                context_used=context,
            )

        # Unclear short reply - might need context
        # Try to relate to pending state
        if state.has_pending_question() or state.has_pending_choices():
            expanded = f"{original} (Context: {state.pending_question or 'pending choices'})"
            return ResolvedIntent(
                type=IntentType.UNCLEAR,
                original_message=original,
                expanded_message=expanded,
                confidence=0.5,
                context_used=context,
            )

        # Truly unclear
        return ResolvedIntent(
            type=IntentType.UNCLEAR,
            original_message=original,
            expanded_message=original,
            confidence=0.3,
            context_used=context,
        )

    def get_message_for_llm(
        self,
        message: str,
        conversation_id: int,
    ) -> str:
        """
        Get the message to send to the LLM.
        If it's a short reply that was resolved, returns expanded version.
        Otherwise returns original.
        """
        resolved = self.resolve(message, conversation_id)

        if resolved.type == IntentType.NORMAL:
            return message

        if resolved.confidence >= 0.7:
            return resolved.expanded_message

        # Low confidence - include both
        context_hint = (
            resolved.context_used.get("pending_question")
            or ("pending choices" if resolved.context_used.get("pending_choices") else "")
            or resolved.context_used.get("next_step")
            or resolved.context_used.get("last_assistant_action")
        )
        if not context_hint:
            return resolved.original_message
        return f"{resolved.original_message}\n[Context: This may refer to {context_hint}]"

    def format_task_state_for_context(
        self,
        conversation_id: int,
    ) -> str:
        """
        Format current task state for inclusion in LLM context.
        This helps the LLM understand what the user might be referring to.
        """
        state = self.store.get_task_state(conversation_id)

        lines = []
        if state.goal:
            lines.append(f"Goal: {state.goal}")
        if state.current_task:
            lines.append(f"Current task: {state.current_task}")
        if state.next_step:
            lines.append(f"Next step: {state.next_step}")
        if state.pending_question:
            lines.append(f"Awaiting answer to: {state.pending_question}")
        if state.pending_choices:
            choices_str = ", ".join(f"{i+1}. {c}" for i, c in enumerate(state.pending_choices))
            lines.append(f"Pending choices: {choices_str}")

        return "\n".join(lines)
