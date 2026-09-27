# backend/tests/test_identity.py
"""
Tests for Identity Loader
=========================
Covers parse_user_md, load(), and get_user_name() resolution.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from backend.identity.loader import load, parse_user_md
from backend.identity.state import UserIdentity, get_user, get_user_name, set_user
from backend.config.settings import settings


@pytest.fixture(autouse=True)
def _reset_snapshot():
    """Each test starts with a clean default snapshot."""
    set_user(UserIdentity())
    yield
    set_user(UserIdentity())


# ---------- parse_user_md ----------

class TestParseUserMd:
    def test_empty_template(self):
        """Template with blank values yields no fields."""
        text = (
            "- **Name:** \n"
            "- **What to call them:** \n"
            "- **Pronouns:** *(optional)*\n"
            "- **Timezone:** \n"
            "- **Notes:** \n"
        )
        fields = parse_user_md(text)
        assert fields == {
            "name": "",
            "call_name": "",
            "pronouns": "",
            "timezone": "",
            "notes": "",
        }

    def test_fully_filled_bold(self):
        """Bold markdown labels parse correctly."""
        text = (
            "- **Name:** Zachary Smith\n"
            "- **What to call them:** Zero\n"
            "- **Pronouns:** he/him\n"
            "- **Timezone:** America/Los_Angeles\n"
            "- **Notes:** Prefers terse replies\n"
        )
        fields = parse_user_md(text)
        assert fields["name"] == "Zachary Smith"
        assert fields["call_name"] == "Zero"
        assert fields["pronouns"] == "he/him"
        assert fields["timezone"] == "America/Los_Angeles"
        assert fields["notes"] == "Prefers terse replies"

    def test_plain_markdown_variant(self):
        """Labels without `**` wrappers also parse."""
        text = (
            "- Name: Zero\n"
            "- What to call them: Z\n"
        )
        fields = parse_user_md(text)
        assert fields["name"] == "Zero"
        assert fields["call_name"] == "Z"

    def test_placeholder_stripped(self):
        """`*(optional)*` italic placeholder is removed from value."""
        text = "- **Pronouns:** *(optional)* they/them\n"
        fields = parse_user_md(text)
        assert fields["pronouns"] == "they/them"

    def test_placeholder_only(self):
        """A value that is only the italic placeholder becomes empty."""
        text = "- **Pronouns:** *(optional)*\n"
        fields = parse_user_md(text)
        assert fields["pronouns"] == ""

    def test_label_case_insensitive(self):
        """Label matching is case-insensitive with whitespace collapsed."""
        text = "- **NAME:**   Zero\n- **what  to  call  them:** Z\n"
        fields = parse_user_md(text)
        assert fields["name"] == "Zero"
        assert fields["call_name"] == "Z"

    def test_unknown_labels_ignored(self):
        """Labels not in the map are skipped."""
        text = "- **Favorite Color:** blue\n- **Name:** Zero\n"
        fields = parse_user_md(text)
        assert fields == {"name": "Zero"}

    def test_malformed_text(self):
        """Free-form text yields no fields, no exceptions."""
        text = "Just some random text\n# A heading\nNot a list at all"
        fields = parse_user_md(text)
        assert fields == {}

    def test_empty_text(self):
        assert parse_user_md("") == {}


# ---------- load() ----------

class TestLoad:
    def test_load_existing_file(self, tmp_path: Path):
        src = tmp_path / "USER.md"
        src.write_text(
            "- **Name:** Zero\n- **What to call them:** Z\n",
            encoding="utf-8",
        )
        snap = load(src)
        assert snap.name == "Zero"
        assert snap.call_name == "Z"
        assert snap.source_path == src
        assert snap.loaded_at > 0

    def test_load_missing_file_falls_back(self, tmp_path: Path):
        """Missing USER.md returns a default snapshot, never raises."""
        missing = tmp_path / "does_not_exist.md"
        snap = load(missing)
        assert snap.name == ""
        assert snap.call_name == ""
        # source_path is None for FileNotFoundError fallback
        assert snap.source_path is None

    def test_load_installs_snapshot(self, tmp_path: Path):
        """After load(), get_user() returns the new snapshot."""
        src = tmp_path / "USER.md"
        src.write_text("- **Name:** Zero\n", encoding="utf-8")
        load(src)
        assert get_user().name == "Zero"

    def test_load_malformed_does_not_abort(self, tmp_path: Path):
        """Malformed file content yields an empty snapshot, no exception."""
        src = tmp_path / "USER.md"
        src.write_text("garbage\n###\nno fields here", encoding="utf-8")
        snap = load(src)
        assert snap.name == ""
        assert snap.call_name == ""


# ---------- get_user_name() resolution ----------

class TestGetUserName:
    def test_call_name_preferred_over_name(self):
        set_user(UserIdentity(name="Zachary", call_name="Z"))
        assert get_user_name() == "Z"

    def test_name_used_when_call_name_empty(self):
        set_user(UserIdentity(name="Zachary", call_name=""))
        assert get_user_name() == "Zachary"

    def test_fallback_when_both_empty(self):
        set_user(UserIdentity(name="", call_name=""))
        assert get_user_name() == settings.user_display_name_fallback

    def test_default_snapshot_returns_fallback(self):
        # Autouse fixture set the default snapshot.
        assert get_user_name() == settings.user_display_name_fallback


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
