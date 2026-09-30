# tests/conftest.py
"""
Shared test setup.

Sarah's code imports itself as `backend.xxx` (the repo lives in a folder
named `backend` on the Creator's PC). This makes that work no matter what
the folder is called, and points the database at a throwaway temp folder.

Run from the repo folder:  python -m pytest tests
"""
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

if "backend" not in sys.modules:
    pkg = types.ModuleType("backend")
    pkg.__path__ = [str(ROOT)]
    sys.modules["backend"] = pkg


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    from backend import db
    monkeypatch.setattr(db, "DATA_DIR", tmp_path)
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    db.init_db()

    from backend import procedure_memory, proactive_engine, screen_timeline
    procedure_memory.init_tables()
    proactive_engine.init_tables()
    screen_timeline.init_tables()
    return tmp_path
