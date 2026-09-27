"""Shared SarahVision ffmpeg path resolution."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Dict

from backend.config import settings as _settings


def _resolve_ffmpeg(screen_dir: Path) -> tuple[Path, str]:
    """Resolve one ffmpeg path for all screen helpers.

    Priority:
    1. SARAHVISION_FFMPEG for backwards compatibility with the screen tool.
    2. SARAH_FFMPEG_PATH / settings.ffmpeg_path, the project-wide source.
    3. A colocated screen/ffmpeg.exe fallback for older installs.
    """
    legacy_override = os.getenv("SARAHVISION_FFMPEG", "").strip()
    if legacy_override:
        return Path(legacy_override), "SARAHVISION_FFMPEG"

    configured = Path(_settings.ffmpeg_path)
    if configured.exists():
        return configured, "SARAH_FFMPEG_PATH"

    return screen_dir / "ffmpeg.exe", "colocated_fallback"


def resolve_ffmpeg_path(screen_dir: Path) -> Path:
    """Resolve one ffmpeg path for all screen helpers."""
    return _resolve_ffmpeg(screen_dir)[0]


def ffmpeg_error_hint(path: Path) -> str:
    return (
        "[SarahVision] ffmpeg.exe not found. "
        f"Resolved path: {path}. "
        "Set SARAH_FFMPEG_PATH for the project-wide ffmpeg path, or "
        "SARAHVISION_FFMPEG for a screen-only override."
    )


def resolve_ffmpeg_diagnostics(screen_dir: Path) -> Dict[str, object]:
    """Return the resolved FFmpeg path, resolution source, and existence hint."""
    path, source = _resolve_ffmpeg(screen_dir)
    exists = path.exists()
    return {
        "ok": exists,
        "resolved_path": str(path),
        "source": source,
        "exists": exists,
        "hint": "" if exists else ffmpeg_error_hint(path),
    }
