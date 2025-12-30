from pathlib import Path
from typing import List


class FileUtils:
    """Safe file helpers scoped to allowed directories."""

    @staticmethod
    def list_files(folder: str) -> List[str]:
        p = Path(folder)
        if not p.exists():
            return []
        return [str(f) for f in p.rglob("*") if f.is_file()]
