"""Tests for multi-file editor helpers."""

import json
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from backend.multi_file_editor import (
    BOILERPLATE_TEMPLATES,
    FileType,
    generate_boilerplate,
)


def test_every_file_type_has_boilerplate_template():
    missing = [file_type.value for file_type in FileType if file_type not in BOILERPLATE_TEMPLATES]
    assert missing == []


def test_config_file_boilerplate_is_real_json_not_todo():
    content = generate_boilerplate(
        FileType.CONFIG_FILE,
        config_name="Sarah",
        description="Sarah runtime config",
    )

    parsed = json.loads(content)
    assert parsed["name"] == "Sarah"
    assert parsed["description"] == "Sarah runtime config"
    assert parsed["settings"] == {}
    assert "TODO" not in content
