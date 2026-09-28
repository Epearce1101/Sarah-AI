"""Tests for the skills loader, runtime, and injection."""
from __future__ import annotations

import sys
import textwrap
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from backend.config.settings import settings
from backend.skills.injection import build_skill_injection
from backend.skills.loader import parse_manifest_file, parse_skill_md
from backend.skills.runtime import discover
from backend.skills.state import SkillManifest, get_skills, set_skills


@pytest.fixture(autouse=True)
def _reset_snapshot():
    set_skills([])
    yield
    set_skills([])


def _write_skill(root: Path, slug: str, name: str, body: str,
                 description: str = "desc", enabled_default: bool = True,
                 fence: bool = True, slug_in_md: str | None = None) -> Path:
    skill_dir = root / slug
    skill_dir.mkdir(parents=True, exist_ok=True)
    md = skill_dir / "SKILL.md"
    front_slug = slug if slug_in_md is None else slug_in_md
    if fence:
        text = textwrap.dedent(f"""\
            ---
            name: {name}
            slug: {front_slug}
            description: {description}
            enabled_default: {"true" if enabled_default else "false"}
            ---

            {body}
        """)
    else:
        text = body
    md.write_text(text, encoding="utf-8")
    return md


# ---------- parse_skill_md ----------

class TestParseSkillMd:
    def test_valid_frontmatter_and_body(self):
        text = "---\nname: Foo\nslug: foo\ndescription: desc\n---\n\nbody text"
        fm, body = parse_skill_md(text)
        assert fm == {"name": "Foo", "slug": "foo", "description": "desc"}
        assert body == "body text"

    def test_missing_fence_returns_none(self):
        fm, body = parse_skill_md("no frontmatter here\n# heading\n")
        assert fm is None
        assert body == ""

    def test_missing_closing_fence_returns_none(self):
        fm, body = parse_skill_md("---\nname: x\n# never closes\n")
        assert fm is None

    def test_quoted_values_unwrapped(self):
        text = '---\nname: "Foo Bar"\nslug: \'foo-bar\'\n---\n'
        fm, _ = parse_skill_md(text)
        assert fm["name"] == "Foo Bar"
        assert fm["slug"] == "foo-bar"

    def test_empty_body_ok(self):
        text = "---\nname: Foo\nslug: foo\ndescription: d\n---\n"
        fm, body = parse_skill_md(text)
        assert fm["slug"] == "foo"
        assert body == ""


# ---------- parse_manifest_file (validation rules) ----------

class TestManifestValidation:
    def test_valid(self, tmp_path: Path):
        path = _write_skill(tmp_path, "good-skill", "Good", "do good")
        result = parse_manifest_file(path)
        assert result is not None
        assert result["slug"] == "good-skill"
        assert result["body"] == "do good"
        assert result["enabled_default"] is True

    def test_slug_directory_mismatch_skipped(self, tmp_path: Path):
        path = _write_skill(tmp_path, "dir-name", "X", "b", slug_in_md="other-slug")
        assert parse_manifest_file(path) is None

    def test_invalid_slug_chars_skipped(self, tmp_path: Path):
        # Directory name has invalid char so the regex itself rejects it.
        path = _write_skill(tmp_path, "BadSlug", "X", "b")
        assert parse_manifest_file(path) is None

    def test_missing_required_field_skipped(self, tmp_path: Path):
        skill_dir = tmp_path / "no-desc"
        skill_dir.mkdir()
        md = skill_dir / "SKILL.md"
        md.write_text("---\nname: X\nslug: no-desc\n---\nbody\n", encoding="utf-8")
        assert parse_manifest_file(md) is None

    def test_no_fence_skipped(self, tmp_path: Path):
        skill_dir = tmp_path / "raw"
        skill_dir.mkdir()
        md = skill_dir / "SKILL.md"
        md.write_text("just some markdown, no frontmatter", encoding="utf-8")
        assert parse_manifest_file(md) is None

    def test_enabled_default_false(self, tmp_path: Path):
        path = _write_skill(tmp_path, "off", "Off", "b", enabled_default=False)
        result = parse_manifest_file(path)
        assert result["enabled_default"] is False


# ---------- discover() integration ----------

class TestDiscover:
    def test_empty_workspace(self, tmp_path: Path):
        # No `skills/` subdir → no non-stale manifests. The shared real DB
        # may still report stale rows left over from earlier tests; those
        # are by-design surfaced, not deleted.
        snap = discover(workspace=tmp_path)
        fresh = [s for s in snap if not s.stale]
        assert fresh == []

    def test_picks_up_valid_manifest(self, tmp_path: Path):
        skills_dir = tmp_path / "skills"
        _write_skill(skills_dir, "alpha", "Alpha", "alpha body")
        snap = discover(workspace=tmp_path)
        assert any(s.slug == "alpha" and not s.stale for s in snap)
        alpha = next(s for s in snap if s.slug == "alpha")
        assert alpha.body == "alpha body"
        assert alpha.enabled is True

    def test_skips_invalid_alongside_valid(self, tmp_path: Path):
        skills_dir = tmp_path / "skills"
        _write_skill(skills_dir, "good", "Good", "ok")
        bad = skills_dir / "bad"
        bad.mkdir()
        (bad / "SKILL.md").write_text("no frontmatter", encoding="utf-8")
        snap = discover(workspace=tmp_path)
        slugs = {s.slug for s in snap if not s.stale}
        assert "good" in slugs
        assert "bad" not in slugs


# ---------- injection (cap enforcement) ----------

class TestInjection:
    def test_empty_when_no_skills(self):
        assert build_skill_injection([]) == ""

    def test_renders_index_not_body(self):
        # The prompt lists what she knows; instructions open on demand (use_skill).
        m = SkillManifest(slug="x", name="X", description="does x things", body="hello world")
        out = build_skill_injection([m])
        assert "- x: does x things" in out and "use_skill" in out
        assert "hello world" not in out

    def test_alphabetical_order(self):
        b = SkillManifest(slug="b", name="B", description="d", body="bb")
        a = SkillManifest(slug="a", name="A", description="d", body="aa")
        out = build_skill_injection([b, a])
        assert out.index("- a:") < out.index("- b:")

    def test_cap_drops_overflow_alphabetically(self, monkeypatch):
        monkeypatch.setattr(settings.__class__, "skills_inject_char_cap", property(lambda self: 600), raising=False)
        skills = [SkillManifest(slug=f"s{i:02d}", name=f"S{i}", description="d" * 250, body="b") for i in range(5)]
        out = build_skill_injection(skills)
        assert "- s00:" in out and "- s04:" not in out
        assert len(out) <= 600

    def test_long_descriptions_are_trimmed(self):
        m = SkillManifest(slug="long", name="L", description="w " * 1000, body="b")
        line = [l for l in build_skill_injection([m]).splitlines() if l.startswith("- long:")][0]
        assert len(line) < 320


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
