"""Tests for the B5 persona loader + injection.

Mirrors the B3 identity-test layout. Exercises:
  - per-slug folder load (`personalities/<slug>/`)
  - empty snapshot when the active slug folder has no files (Issue #23: the
    legacy top-level IDENTITY/SOUL fallback was removed)
  - empty snapshot when no state file / no slug
  - malformed `_personality_state.json` degrades gracefully (no crash)
  - `persona_use_active_state=False` skips the state file
  - `reload()` swaps the snapshot
  - injection cap truncates SOUL but preserves Sarah lock + body awareness
  - `persona_enabled=False` returns empty injection
  - both prompt-injection sites render the persona consistently
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from backend.persona.loader import load, reload
from backend.persona.injection import build_persona_injection
from backend.persona.state import PersonaSnapshot, get_persona, set_persona


@pytest.fixture(autouse=True)
def _reset_snapshot():
    set_persona(PersonaSnapshot())
    yield
    set_persona(PersonaSnapshot())


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    """Build a workspace skeleton matching the OpenClaw layout."""
    ws = tmp_path / "workspace"
    (ws / "personalities" / "jessie").mkdir(parents=True)
    (ws / "personalities" / "default").mkdir(parents=True)
    return ws


# ---------- per-slug folder load ----------

class TestPerSlugLoad:
    def test_loads_per_slug_files_when_state_active(self, workspace: Path):
        (workspace / "personalities" / "_personality_state.json").write_text(
            '{"active_personality": "jessie"}', encoding="utf-8"
        )
        (workspace / "personalities" / "jessie" / "IDENTITY.md").write_text(
            "JESSIE_IDENTITY_MARKER", encoding="utf-8"
        )
        (workspace / "personalities" / "jessie" / "SOUL.md").write_text(
            "JESSIE_SOUL_MARKER", encoding="utf-8"
        )

        snap = load(workspace)
        assert snap.slug == "jessie"
        assert "JESSIE_IDENTITY_MARKER" in snap.identity_md
        assert "JESSIE_SOUL_MARKER" in snap.soul_md
        assert snap.fallback_used is False
        assert snap.source_dir == workspace / "personalities" / "jessie"

    def test_loads_per_slug_files_with_only_soul(self, workspace: Path):
        """Per-slug folder with only SOUL.md is fine — IDENTITY can be empty."""
        (workspace / "personalities" / "_personality_state.json").write_text(
            '{"active_personality": "jessie"}', encoding="utf-8"
        )
        (workspace / "personalities" / "jessie" / "SOUL.md").write_text(
            "ONLY_SOUL", encoding="utf-8"
        )

        snap = load(workspace)
        assert snap.slug == "jessie"
        assert snap.identity_md == ""
        assert "ONLY_SOUL" in snap.soul_md
        assert snap.fallback_used is False


# ---------- no top-level fallback (Issue #23) ----------

class TestNoTopLevelFallback:
    def test_no_fallback_to_top_level_when_per_slug_missing(self, workspace: Path):
        """Active slug with an empty/missing folder → empty snapshot.

        Issue #23 removed the legacy top-level fallback, so top-level
        IDENTITY/SOUL files are intentionally ignored.
        """
        (workspace / "personalities" / "_personality_state.json").write_text(
            '{"active_personality": "ghost"}', encoding="utf-8"
        )
        # No `personalities/ghost/` folder. Top-level files must NOT be loaded.
        (workspace / "IDENTITY.md").write_text("TOPLEVEL_IDENTITY", encoding="utf-8")
        (workspace / "SOUL.md").write_text("TOPLEVEL_SOUL", encoding="utf-8")

        snap = load(workspace)
        assert snap.slug == "ghost"
        assert snap.identity_md == ""
        assert snap.soul_md == ""
        assert snap.fallback_used is False
        assert snap.source_dir is None

    def test_no_state_file_yields_empty_even_with_top_level(self, workspace: Path):
        """No state file → no slug → top-level files are ignored (no fallback)."""
        (workspace / "IDENTITY.md").write_text("TL_ID", encoding="utf-8")
        (workspace / "SOUL.md").write_text("TL_SOUL", encoding="utf-8")

        snap = load(workspace)
        assert snap.slug == ""
        assert snap.identity_md == ""
        assert snap.soul_md == ""
        assert snap.source_dir is None

    def test_empty_snapshot_when_no_files_anywhere(self, workspace: Path):
        snap = load(workspace)
        assert snap.slug == ""
        assert snap.identity_md == ""
        assert snap.soul_md == ""
        assert snap.source_dir is None

    def test_malformed_state_json_does_not_abort(self, workspace: Path):
        """Malformed state JSON must degrade gracefully — no crash, empty snapshot."""
        (workspace / "personalities" / "_personality_state.json").write_text(
            "this is not json {{{", encoding="utf-8"
        )
        # Per-slug content would normally load, but the slug can't be read.
        (workspace / "IDENTITY.md").write_text("RECOVERED", encoding="utf-8")

        snap = load(workspace)
        assert snap.slug == ""
        assert snap.identity_md == ""


# ---------- settings flags ----------

class TestSettings:
    def test_use_active_state_disabled_skips_per_slug(self, workspace: Path):
        """When `persona_use_active_state=False`, the state file is ignored.

        With no slug resolved and no top-level fallback (Issue #23), the result
        is an empty snapshot — per-slug content is never reached.
        """
        (workspace / "personalities" / "_personality_state.json").write_text(
            '{"active_personality": "jessie"}', encoding="utf-8"
        )
        (workspace / "personalities" / "jessie" / "SOUL.md").write_text(
            "JESSIE_PER_SLUG", encoding="utf-8"
        )

        with patch("backend.persona.loader.settings") as mock_settings:
            mock_settings.persona_use_active_state = False
            mock_settings.openclaw_workspace_path = workspace
            snap = load(workspace)

        assert snap.slug == ""
        assert snap.soul_md == ""
        assert snap.identity_md == ""


# ---------- reload ----------

class TestReload:
    def test_reload_picks_up_new_content(self, workspace: Path):
        (workspace / "personalities" / "_personality_state.json").write_text(
            '{"active_personality": "jessie"}', encoding="utf-8"
        )
        identity = workspace / "personalities" / "jessie" / "IDENTITY.md"
        identity.write_text("VERSION_1", encoding="utf-8")
        snap1 = load(workspace)
        assert "VERSION_1" in snap1.identity_md

        identity.write_text("VERSION_2", encoding="utf-8")
        snap2 = load(workspace)
        assert "VERSION_2" in snap2.identity_md
        assert "VERSION_1" not in snap2.identity_md


# ---------- injection ----------

class TestInjection:
    def test_empty_snapshot_returns_empty_injection(self):
        set_persona(PersonaSnapshot())
        assert build_persona_injection() == ""

    def test_injection_includes_both_blocks(self):
        set_persona(PersonaSnapshot(
            slug="x",
            identity_md="ID_BODY",
            soul_md="SOUL_BODY",
        ))
        out = build_persona_injection()
        assert "# Sarah Identity Lock" in out
        assert "# Legacy Persona Source" in out
        assert "ID_BODY" in out
        assert "# Voice Style Source" in out
        assert "SOUL_BODY" in out

    def test_injection_rewrites_legacy_jessie_alias(self):
        set_persona(PersonaSnapshot(
            slug="jessie",
            identity_md="Name: Jessie",
            soul_md="JessieBot is affectionate.",
        ))
        out = build_persona_injection()
        assert "Name: Sarah" in out
        assert "Sarah is affectionate." in out

    def test_disabled_returns_empty(self):
        set_persona(PersonaSnapshot(
            slug="x",
            identity_md="ID_BODY",
            soul_md="SOUL_BODY",
        ))
        with patch("backend.persona.injection.settings") as mock_settings:
            mock_settings.persona_enabled = False
            assert build_persona_injection() == ""

    def test_cap_truncates_soul_preserves_identity(self):
        from backend.persona.injection import _AGENCY, _IDENTITY_LOCK, _BODY_AWARENESS, _TRUNC_MARKER

        identity = "IDENTITY_KEEP" * 5          # 65 chars
        soul = "SOUL_BODY_PADDING_" * 400        # ~7200 chars — must be trimmed
        set_persona(PersonaSnapshot(
            slug="x",
            identity_md=identity,
            soul_md=soul,
        ))
        # The cap is for the persona sources only: the identity lock, body
        # awareness and agency blocks are always there in full. Size it so a
        # *partial* slice of SOUL survives, exercising the trim path.
        cap = len(identity) + 400
        with patch("backend.persona.injection.settings") as mock_settings:
            mock_settings.persona_enabled = True
            mock_settings.persona_inject_char_cap = cap
            out = build_persona_injection()

        # Operating instructions and the identity body preserved verbatim.
        assert _IDENTITY_LOCK in out and _BODY_AWARENESS in out and _AGENCY in out
        assert "IDENTITY_KEEP" in out
        # SOUL got hard-trimmed: marker present, and the full soul is NOT included.
        assert "[truncated]" in out
        assert "SOUL_BODY_PADDING_" in out          # a partial slice survives
        assert out.count("SOUL_BODY_PADDING_") < 400  # but not the whole thing
        operating = len(_IDENTITY_LOCK) + len(_BODY_AWARENESS) + len(_AGENCY)
        assert len(out) <= operating + cap + 120

    def test_new_abilities_never_squeeze_out_the_soul(self):
        # A realistically sized persona must survive whole at the default cap,
        # however long her tool instructions grow.
        set_persona(PersonaSnapshot(slug="x", identity_md="I" * 1500, soul_md="S" * 5000))
        out = build_persona_injection()
        assert "[truncated]" not in out and out.count("S") >= 5000


# ---------- prompt-site integration ----------

class TestPromptSiteIntegration:
    """Verify both injection sites read the same persona block at request time."""

    def test_context_builder_renders_persona_block(self, workspace: Path):
        """ContextBuilder.SYSTEM_PROMPT_TEMPLATE includes the persona injection."""
        (workspace / "personalities" / "_personality_state.json").write_text(
            '{"active_personality": "jessie"}', encoding="utf-8"
        )
        (workspace / "personalities" / "jessie" / "IDENTITY.md").write_text(
            "CB_TEST_IDENTITY_MARKER", encoding="utf-8"
        )
        (workspace / "personalities" / "jessie" / "SOUL.md").write_text(
            "CB_TEST_SOUL_MARKER", encoding="utf-8"
        )
        load(workspace)

        from backend.memory.context_builder import ContextBuilder
        # We don't need a real conversation_id — just verify the format string
        # accepts `persona_block` as a key. Pull the raw rendered template.
        rendered = ContextBuilder.SYSTEM_PROMPT_TEMPLATE.format(
            persona_block=build_persona_injection(),
            user_name="TestUser",
            time_context="(time)",
        )
        assert "CB_TEST_IDENTITY_MARKER" in rendered
        assert "CB_TEST_SOUL_MARKER" in rendered
        assert "TestUser" in rendered

    def test_multi_agent_assembles_persona_block(self, workspace: Path):
        """The multi_agent system_hint composition includes persona content."""
        (workspace / "personalities" / "_personality_state.json").write_text(
            '{"active_personality": "jessie"}', encoding="utf-8"
        )
        (workspace / "personalities" / "jessie" / "IDENTITY.md").write_text(
            "MA_TEST_IDENTITY", encoding="utf-8"
        )
        (workspace / "personalities" / "jessie" / "SOUL.md").write_text(
            "MA_TEST_SOUL", encoding="utf-8"
        )
        load(workspace)

        # Recreate the multi_agent composition steps without invoking the LLM.
        persona_block = build_persona_injection()
        creator_note = "Creator note: you are talking to your TestUser."
        rules = "Intent: chat (confidence: 0.9)\n\nRules:\n- be concise"
        system_hint = "\n\n".join(p for p in [persona_block, creator_note, rules] if p)

        assert "MA_TEST_IDENTITY" in system_hint
        assert "MA_TEST_SOUL" in system_hint
        assert "Creator note" in system_hint
