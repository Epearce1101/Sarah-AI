"""Regression coverage for post-phase backlog follow-up slices."""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from backend.identity.loader import save_user_md
from backend.openclaw_memory import (
    build_openclaw_memory_injection,
    load_openclaw_memory,
)
from backend.persona.loader import switch as switch_persona
from backend.sarah_core import SarahCore
from backend.screen.ffmpeg_paths import resolve_ffmpeg_diagnostics, resolve_ffmpeg_path
from backend.skills import installer as skill_installer
from backend.skills.hub import build_hub_index
from backend.skills.permissions import describe_permission_policy
from backend.skills.state import SkillManifest


def test_identity_write_back_persists_user_md(tmp_path):
    user_md = tmp_path / "USER.md"
    snapshot = save_user_md(
        {
            "name": "Zero Test",
            "call_name": "Zero",
            "pronouns": "he/him",
            "timezone": "America/Denver",
            "notes": "prefers direct answers",
        },
        path=user_md,
    )

    text = user_md.read_text(encoding="utf-8")
    assert "- **Name:** Zero Test" in text
    assert snapshot.call_name == "Zero"
    assert snapshot.timezone == "America/Denver"


def test_persona_switch_writes_active_state(tmp_path):
    persona_dir = tmp_path / "personalities" / "jessie"
    persona_dir.mkdir(parents=True)
    (persona_dir / "IDENTITY.md").write_text("Jessie identity", encoding="utf-8")

    snapshot = switch_persona("jessie", workspace=tmp_path)

    assert snapshot.slug == "jessie"
    assert snapshot.identity_md == "Jessie identity"
    assert '"active_personality": "jessie"' in (
        tmp_path / "personalities" / "_personality_state.json"
    ).read_text(encoding="utf-8")


def test_skill_install_from_url_writes_valid_skill(monkeypatch, tmp_path):
    skill_text = "\n".join([
        "---",
        "name: Installed Skill",
        "slug: installed-skill",
        "description: Installed from a URL.",
        "enabled_default: false",
        "---",
        "Body marker.",
        "",
    ])
    monkeypatch.setattr(skill_installer, "_download_text", lambda _url: skill_text)

    result = skill_installer.install_skill_from_url(
        "https://example.test/SKILL.md",
        workspace=tmp_path,
    )

    target = tmp_path / "skills" / "installed-skill" / "SKILL.md"
    assert result["slug"] == "installed-skill"
    assert target.read_text(encoding="utf-8") == skill_text
    with pytest.raises(FileExistsError):
        skill_installer.install_skill_from_url(
            "https://example.test/SKILL.md",
            workspace=tmp_path,
        )


def test_screen_ffmpeg_resolver_honors_legacy_override(monkeypatch, tmp_path):
    override = tmp_path / "ffmpeg.exe"
    monkeypatch.setenv("SARAHVISION_FFMPEG", str(override))
    assert resolve_ffmpeg_path(tmp_path) == override
    diag = resolve_ffmpeg_diagnostics(tmp_path)
    assert diag["source"] == "SARAHVISION_FFMPEG"
    assert diag["resolved_path"] == str(override)


def test_legacy_conversation_summary_uses_live_engines():
    stub = SimpleNamespace(
        task_engine=SimpleNamespace(summarize_tasks_for_prompt=lambda: "Task A"),
        reflection_engine=SimpleNamespace(build_reflection_summary=lambda: "Rule B"),
        memory_enabled=True,
    )

    summary = SarahCore._build_conversation_summary(stub)

    assert "Active tasks:\nTask A" in summary
    assert "Self-improvement notes:\nRule B" in summary
    assert "Conversation memory system is enabled" in summary


def test_openclaw_memory_md_is_read_only_prompt_context(tmp_path):
    (tmp_path / "MEMORY.md").write_text("Creator likes concise updates.", encoding="utf-8")

    snapshot = load_openclaw_memory(workspace=tmp_path)
    block = build_openclaw_memory_injection(workspace=tmp_path)

    assert snapshot.exists is True
    assert "Creator likes concise updates." in block
    assert "never overwrites Sarah DB memories" in block


def test_skills_hub_and_permission_policy_are_explicit():
    installed = [SkillManifest(slug="using-superpowers", name="Using Superpowers", description="d", body="b")]
    hub = build_hub_index(installed)
    policy = describe_permission_policy()

    assert any(item["slug"] == "using-superpowers" and item["installed"] for item in hub)
    assert policy["execution_default"] == "disabled"
