from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from backend import db
from backend.db import get_connection, init_db
from backend.diagnostics.telemetry import (
    build_telemetry_snapshot,
    record_chat_result,
    record_voice_latency,
)


def test_diagnostics_telemetry_snapshot_has_live_metrics(tmp_path):
    old_db = db.DB_PATH
    db.DB_PATH = tmp_path / "sarah-telemetry.db"
    try:
        init_db()
        conn = get_connection()
        cur = conn.cursor()
        cur.execute("INSERT INTO conversations (title) VALUES ('Telemetry')")
        conversation_id = cur.lastrowid
        cur.execute(
            "INSERT INTO messages (conversation_id, role, content) VALUES (?, 'user', 'thanks sarah this is perfect')",
            (conversation_id,),
        )
        cur.execute(
            "INSERT INTO messages (conversation_id, role, content) VALUES (?, 'assistant', 'I am here.')",
            (conversation_id,),
        )
        cur.execute(
            "INSERT INTO logs (level, source, message) VALUES ('ERROR', 'unit-test', 'simulated')"
        )
        conn.commit()
        conn.close()

        record_chat_result(
            message="hello sarah",
            reply="hello",
            tokens_used=42,
            token_budget=3072,
            latency_ms=125.0,
            model="unit-test-model",
        )
        record_voice_latency("stt", 33.0, ok=True)
        record_voice_latency("tts", 44.0, ok=True)

        snapshot = build_telemetry_snapshot()
        metrics = snapshot["metrics"]

        assert snapshot["ok"] is True
        assert metrics["chat.conversation_count"] == 1
        assert metrics["chat.messages_day"] >= 2
        assert metrics["model.top_model"] == "unit-test-model"
        assert metrics["ai.tokens_day"] >= 42
        assert metrics["ai.total_response_time_ms"] >= 125
        assert metrics["voice.stt_latency_ms"] == 33.0
        assert metrics["voice.tts_generation_ms"] == 44.0
        assert "system.cpu_pct" in metrics
        assert "latencyBuckets" in snapshot["distributions"]
    finally:
        db.DB_PATH = old_db
