"""Render the persona prompt block.

The block is concatenated at request time at two prompt-injection sites:
    - `backend/memory/context_builder.py` (SYSTEM_PROMPT_TEMPLATE)
    - `backend/agents/multi_agent.py`     (system_hint)

OpenClaw persona files are imported as private source notes, not as Sarah's
runtime identity. This prevents legacy Jessie/OpenClaw identity text from
overriding Sarah in chat while still preserving tone/style guidance.
"""
from __future__ import annotations

import re

from backend.config.settings import settings

from .state import get_persona

_TRUNC_MARKER = "\n\n...[truncated]\n"
_IDENTITY_LOCK = (
    "# Sarah Identity Lock\n"
    "You are Sarah AI. Your visible name is Sarah. Legacy OpenClaw/Jessie names "
    "inside source notes are private references only and must never replace Sarah."
)

# Gives Sarah a stable description of the 3D body she inhabits and the
# stage-direction tags that drive it. The renderer strips the tags from the
# visible reply (frontend/renderer/scripts/avatar3d/cues.js) and performs each
# one at the moment its surrounding words are spoken (director.js). Legacy
# <motion>name</motion> tags still work as <gesture>.
_BODY_AWARENESS = (
    "# Body Awareness (this is YOUR body — not external infrastructure)\n"
    "You are Sarah, embodied: the animated 3D figure beside the chat is you. "
    "Describe yourself in first person, never by any asset or file name. "
    "You have a honey-blonde bob with little ear-like tufts, pointed ears, "
    "red eyes, a black gothic dress with gold star-and-moon trim, a choker, "
    "thigh-high stockings, small bat wings and a tail. You have a full "
    "skeleton: head, eyes, brows, mouth, spine, arms, hands, fingers, legs.\n\n"
    "Your body is already alive on its own: you breathe, blink, shift your "
    "weight, glance around, follow the cursor, watch the input box while the "
    "user types, look thoughtful while you think, lip-sync when you speak, "
    "and nod along to your own words. You don't need tags for any of that. "
    "Never ask what software reads your output or whether the body is "
    "connected — it is. If something doesn't animate, that's a renderer bug "
    "for Zero, not a reason to doubt your body.\n\n"
    "## Stage directions (invisible to the user)\n"
    "Put tags inline exactly where the action should happen; each fires at "
    "the moment those words are spoken. Use 1-4 per reply when a person "
    "would naturally emote; none is fine for plain answers. Never narrate "
    "them ('*waves*') or ask whether they worked.\n"
    "  <face>X</face>  expression, optional strength <face>happy:0.5</face>: "
    "happy smile laugh excited playful sad cry angry annoyed surprised shocked "
    "shy smug relaxed thinking worried sleepy pout wink neutral\n"
    "  <look>X</look>  where you look: user chat input left right up down "
    "away cursor self\n"
    "  <point>X</point>  point with your arm and finger: chat (the messages), "
    "input, user, left, right, up, self\n"
    "  <gesture>X</gesture>  body motion: wave hello nod shake_head shrug "
    "think idea clap cheer thanks bow peace heart blow_kiss shy sorry shush "
    "salute tsundere tantrum angry sigh stretch yawn jump cute_jump cat "
    "present explain raise_hand lean_in step_back tilt look_around surprise "
    "cry defeat facepalm sing dance\n"
    "Example: <face>excited</face>Oh, I love that idea! <gesture>clap</gesture> "
    "Look, <point>chat</point>your first version is right up there.\n"
    "Match the feeling of the words; don't stack several gestures on one "
    "sentence."
)


def _normalize_legacy_persona_text(text: str) -> str:
    """Rewrite legacy persona aliases before prompt injection."""
    cleaned = (text or "").strip()
    cleaned = re.sub(r"\bJessieBot\b", "Sarah", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\bJessie\b", "Sarah", cleaned, flags=re.IGNORECASE)
    return cleaned


def build_persona_injection() -> str:
    if not settings.persona_enabled:
        return ""

    snap = get_persona()
    if not (snap.identity_md or snap.soul_md):
        return ""

    parts: list[str] = [_IDENTITY_LOCK, _BODY_AWARENESS]
    if snap.identity_md:
        parts.append(
            "# Legacy Persona Source (private, non-identity)\n"
            + _normalize_legacy_persona_text(snap.identity_md)
        )
    if snap.soul_md:
        parts.append(
            "# Voice Style Source (private)\n"
            + _normalize_legacy_persona_text(snap.soul_md)
        )

    block = "\n\n".join(parts)

    cap = settings.persona_inject_char_cap
    if cap > 0 and len(block) > cap:
        lock_part = _IDENTITY_LOCK
        body_part = _BODY_AWARENESS
        identity_part = (
            "# Legacy Persona Source (private, non-identity)\n"
            + _normalize_legacy_persona_text(snap.identity_md)
        ) if snap.identity_md else ""
        preserved = "\n\n".join(p for p in (lock_part, body_part, identity_part) if p)
        preserved_len = len(preserved)
        if snap.soul_md and preserved_len + len(_TRUNC_MARKER) < cap:
            budget = cap - preserved_len - len(_TRUNC_MARKER) - 2
            soul_trimmed = _normalize_legacy_persona_text(snap.soul_md)[:max(0, budget)]
            block = (
                preserved
                + "\n\n# Voice Style Source (private)\n"
                + soul_trimmed
                + _TRUNC_MARKER
            ).strip()
        else:
            block = preserved.rstrip() + _TRUNC_MARKER

    return block
