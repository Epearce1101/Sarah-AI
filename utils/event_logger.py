from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Literal


EventLevel = Literal["INFO", "WARN", "ERROR"]


class EventLogger:
    def __init__(self, log_path: Path):
        self.log_path = Path(log_path)
        self.log_path.parent.mkdir(parents=True, exist_ok=True)

    def log(self, level: EventLevel, message: str):
        ts = datetime.utcnow().isoformat()
        line = f"[{ts}] [{level}] {message}\n"
        with self.log_path.open("a", encoding="utf-8") as f:
            f.write(line)
