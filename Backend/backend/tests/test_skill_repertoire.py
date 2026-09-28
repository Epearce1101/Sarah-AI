"""Her skill repertoire: OpenClaw/Agent-Skills installs, only her own folder."""
import asyncio
import io
import json
import zipfile
from pathlib import Path

import pytest

from backend.config.settings import settings
from backend.skills import installer, reload
from backend.skills.loader import parse_manifest_file, parse_skill_md

OPENCLAW_SKILL = """---
name: weather-check
description: >
  Look up the current weather for a city using the free wttr.in service.
  Use when the user asks about weather.
metadata:
  openclaw:
    emoji: "🌦"
    requires: {"bins": ["curl"]}
---
# Weather
Run `scripts/weather.py <city>` and summarise the result.
"""


@pytest.fixture
def repertoire(tmp_path, monkeypatch):
    root = tmp_path / "repertoire"
    monkeypatch.setattr(settings.__class__, "skills_path", property(lambda self: root), raising=False)
    yield root
    reload_safe()


def reload_safe():
    try:
        reload()
    except Exception:
        pass


def _skill_folder(base: Path, name="weather-check") -> Path:
    folder = base / name
    (folder / "scripts").mkdir(parents=True)
    (folder / "SKILL.md").write_text(OPENCLAW_SKILL, encoding="utf-8")
    (folder / "scripts" / "weather.py").write_text("print('sunny')\n", encoding="utf-8")
    return folder


def test_openclaw_frontmatter_without_slug_is_accepted(tmp_path):
    fm, body = parse_skill_md(OPENCLAW_SKILL)
    assert fm["name"] == "weather-check"
    assert fm["description"].startswith("Look up the current weather") and "Use when" in fm["description"]
    assert body.startswith("# Weather")
    folder = _skill_folder(tmp_path)
    manifest = parse_manifest_file(folder / "SKILL.md")
    assert manifest["slug"] == "weather-check"


def test_install_from_local_folder_copies_scripts(tmp_path, repertoire):
    src = _skill_folder(tmp_path / "downloads")
    out = installer.install_skill(str(src))
    assert out["slug"] == "weather-check"
    assert (repertoire / "weather-check" / "scripts" / "weather.py").exists()
    assert "scripts/weather.py" in out["files"]
    with pytest.raises(FileExistsError):
        installer.install_skill(str(src))
    assert installer.install_skill(str(src), overwrite=True)["slug"] == "weather-check"


def test_install_from_zip_is_safe(tmp_path, repertoire):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("pack/weather-check/SKILL.md", OPENCLAW_SKILL)
        zf.writestr("pack/weather-check/scripts/weather.py", "print('sunny')")
        zf.writestr("../../evil.txt", "should never be written")
    zpath = tmp_path / "skill.zip"
    zpath.write_bytes(buf.getvalue())
    out = installer.install_skill(str(zpath))
    assert out["slug"] == "weather-check" and "scripts/weather.py" in out["files"]
    assert not (tmp_path.parent / "evil.txt").exists()


def test_install_from_github_folder(repertoire, monkeypatch):
    calls = []

    def fake_get(url, timeout=30, accept=None):
        calls.append(url)
        if "/contents/skills/weather-check/scripts" in url:
            return json.dumps([{"type": "file", "name": "weather.py", "size": 10,
                                "download_url": "https://raw/weather.py"}]).encode()
        if "/contents/skills/weather-check" in url:
            return json.dumps([
                {"type": "file", "name": "SKILL.md", "size": 300, "download_url": "https://raw/SKILL.md"},
                {"type": "dir", "name": "scripts", "path": "skills/weather-check/scripts"},
            ]).encode()
        if url == "https://raw/SKILL.md":
            return OPENCLAW_SKILL.encode()
        if url == "https://raw/weather.py":
            return b"print('sunny')"
        raise AssertionError(url)

    monkeypatch.setattr(installer, "_get", fake_get)
    out = installer.install_skill("https://github.com/someone/skills/tree/main/skills/weather-check")
    assert out["slug"] == "weather-check"
    assert (repertoire / "weather-check" / "scripts" / "weather.py").read_text() == "print('sunny')"
    assert any("ref=main" in c for c in calls)


def test_repo_url_lists_the_skills_in_it(repertoire, monkeypatch):
    def fake_get(url, timeout=30, accept=None):
        if "/contents/" in url:
            return json.dumps([{"type": "file", "name": "README.md", "size": 5, "download_url": "https://raw/README.md"}]).encode()
        if url == "https://raw/README.md":
            return b"hi"
        if url.endswith("/repos/someone/skills"):
            return json.dumps({"default_branch": "main"}).encode()
        if "/git/trees/" in url:
            return json.dumps({"tree": [{"path": "skills/a/SKILL.md"}, {"path": "skills/b/SKILL.md"}, {"path": "README.md"}]}).encode()
        raise AssertionError(url)

    calls = []
    monkeypatch.setattr(installer, "_get", lambda url, timeout=30, accept=None: calls.append(url) or fake_get(url))
    with pytest.raises(LookupError) as err:
        installer.install_skill("https://github.com/someone/skills")
    assert "tree/main/skills/a" in str(err.value) and "tree/main/skills/b" in str(err.value)
    # Only the top level is listed: a repository is never downloaded whole.
    assert sum("/contents/" in c for c in calls) == 1


def test_a_wrong_folder_says_how_to_find_the_right_one(repertoire, monkeypatch):
    def fake_get(url, timeout=30, accept=None):
        raise ValueError(f"failed to fetch {url}: HTTP Error 404: Not Found")

    monkeypatch.setattr(installer, "_get", fake_get)
    with pytest.raises(ValueError) as err:
        installer.install_skill("https://github.com/someone/skills/tree/main/skill-creator")
    assert "add_skill https://github.com/someone/skills" in str(err.value)


def test_she_learns_opens_and_forgets_a_skill(tmp_path, repertoire):
    from backend.agency import tools

    src = _skill_folder(tmp_path / "downloads")
    run = lambda name, args: asyncio.run(tools.call(name, args))
    learned = run("add_skill", {"source": str(src)})
    assert learned["ok"] and "weather-check" in learned["result"]
    listed = run("list_my_skills", {})
    assert "weather-check" in listed["result"]
    opened = json.loads(run("use_skill", {"name": "weather-check"})["result"])
    assert opened["instructions"].startswith("# Weather") and "scripts/weather.py" in opened["files"]
    assert opened["folder"].startswith(str(repertoire))
    assert "don't have a skill" in run("use_skill", {"name": "nope"})["result"]
    assert run("remove_skill", {"name": "weather-check"})["ok"]
    assert not (repertoire / "weather-check").exists()


def test_only_her_own_folder_is_read(tmp_path, repertoire, monkeypatch):
    """Skills elsewhere (e.g. an OpenClaw workspace) are never picked up."""
    outside = tmp_path / "openclaw" / "workspace" / "skills"
    _skill_folder(outside)
    monkeypatch.setattr(settings.__class__, "openclaw_workspace_path", property(lambda self: tmp_path / "openclaw" / "workspace"), raising=False)
    snapshot = reload()
    assert not any(s.slug == "weather-check" and not s.stale for s in snapshot)
    from backend.skills import get_enabled_skills
    assert not any(s.slug == "weather-check" for s in get_enabled_skills())
    assert repertoire.exists() and not any(repertoire.iterdir())


def test_skill_without_a_name_is_named_after_its_folder(tmp_path, repertoire):
    src = tmp_path / "desktop_control"
    src.mkdir()
    (src / "SKILL.md").write_text("---\ndescription: Advanced desktop automation\n---\n# Desktop\nUse pyautogui.\n",
                                  encoding="utf-8")
    out = installer.install_skill(str(src))
    assert out["slug"] == "desktop-control" and out["name"] == "desktop control"
