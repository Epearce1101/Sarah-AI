import json
from pathlib import Path
from typing import Dict, Any


class MemoryPalace:
    """File-based key/value memory for Sarah."""

    def __init__(self, drive_path: Path):
        self.path = Path(drive_path) / "memory" / "memory_graph.json"
        self.path.parent.mkdir(parents=True, exist_ok=True)

        if self.path.exists():
            try:
                self.graph: Dict[str, Any] = json.loads(self.path.read_text(encoding="utf-8"))
            except Exception:
                self.graph = {}
        else:
            self.graph = {}
            self._save()

    def _save(self):
        self.path.write_text(json.dumps(self.graph, indent=2), encoding="utf-8")

    def store(self, key: str, value: Any):
        self.graph[key] = value
        self._save()

    def recall(self, key: str) -> Any:
        return self.graph.get(key)
