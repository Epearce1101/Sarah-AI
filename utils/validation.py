from pathlib import Path
from typing import Dict, Any


class Validator:
    """Simple backend validator to ensure key dirs exist."""

    def __init__(self, root: Path):
        self.root = Path(root)

    def ensure_directories(self) -> Dict[str, Any]:
        drive = self.root / "SARAH_DRIVE"
        (drive / "identity").mkdir(parents=True, exist_ok=True)
        (drive / "memory").mkdir(parents=True, exist_ok=True)
        (drive / "logs").mkdir(parents=True, exist_ok=True)
        return {
            "sarah_drive": str(drive),
            "identity": str(drive / "identity"),
            "memory": str(drive / "memory"),
            "logs": str(drive / "logs"),
        }
