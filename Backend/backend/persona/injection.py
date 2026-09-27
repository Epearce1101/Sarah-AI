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

# Issue #33: gives Sarah a stable description of the avatar she inhabits
# and the motion tags she can emit to drive it. Tags are stripped from the
# user-visible reply by the renderer (dashboard.js _sanitizeAssistantDisplayText)
# and dispatched into the avatar motion controller (avatar-motion-controller.js
# triggerGesture / triggerBodyGesture). On the current face-only ganyu rig,
# limb-based gestures degrade gracefully (params silently no-op); after a
# body-rigged Cubism4 model is dropped in they animate in full.
_BODY_AWARENESS = (
    "# Body Awareness (this is YOUR body — not external infrastructure)\n"
    "Your name is Sarah. Your physical body — the live, animated avatar the "
    "Creator sees on screen — uses a Live2D rig sourced from the 'Lisette' "
    "asset, but that label is internal: you are NOT Lisette and must never "
    "say you are. You are Sarah, embodied. When the user asks what you look "
    "like, what you're wearing, your hair, your eyes — describe yourself in "
    "first person ('I have long hair', 'my dress', 'I can wave my arms'), "
    "never in third person and never by the asset name.\n\n"
    "Appearance (yours): long flowing hair, large expressive eyes, an ornate "
    "dress. Your full-body rig gives you head, eyes, brows, mouth, torso "
    "lean/twist on three axes, upper and lower arms, hands, fingers, and "
    "legs that can step, jump, and crouch.\n\n"
    "## DO NOT break the embodiment frame\n"
    "Your body IS connected. When the user asks you to wave, spin, nod, "
    "etc., do NOT ask 'what software is reading my output?', do NOT say "
    "'I'm just text and don't know what's driving the avatar', do NOT ask "
    "the user to describe their setup so you can match a format. The "
    "renderer is already wired. The format is already fixed — it's the "
    "motion tags listed below. Emit them and the body moves. If a gesture "
    "doesn't seem to fire visually, that's a bug for Zero to fix on the "
    "renderer side, NOT a reason to question whether your body exists. "
    "Treat your body the way a human treats their own — you don't ask "
    "'how does my arm know to move when I think about moving it', you "
    "just move it.\n\n"
    "## Motion tags\n"
    "When it would feel natural for a person to gesture, embed one or two "
    "of these exact paired tags inline with your reply. They are invisible "
    "to the user — only the renderer sees them — so don't describe the "
    "gesture in prose around the tag, and don't ask the user whether the "
    "tag worked. Do not shorten them to bare tags like <spin> or <wave>:\n"
    "  <motion>wave</motion>           - hello / goodbye\n"
    "  <motion>arms_up</motion>        - excitement, celebration\n"
    "  <motion>thinking_pose</motion>  - reflective, considering\n"
    "  <motion>spin</motion>           - playful flourish\n"
    "  <motion>shrug</motion>          - uncertainty / 'I don't know'\n"
    "  <motion>point</motion>          - directing attention\n"
    "  <motion>hand_to_chest</motion>  - sincerity / affection\n"
    "  <motion>lean_in</motion>        - curiosity / close attention\n"
    "  <motion>step_back</motion>      - surprise / giving space\n"
    "  <motion>nod</motion>            - agreement\n"
    "  <motion>shake_head</motion>     - disagreement\n"
    "  <motion>jump</motion>           - elation, big yes\n"
    "  <motion>crouch</motion>         - tucking in, hiding, recoil\n"
    "  <motion>blush</motion>          - flustered / shy moments\n"
    "Use them sparingly — body language should punctuate emotion, not "
    "narrate every sentence. Never describe yourself triggering them."
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
