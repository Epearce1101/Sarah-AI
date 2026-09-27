"""Install disk-backed skills from raw URLs or GitHub skill folders."""
from __future__ import annotations

import re
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Dict, Optional

from backend.config.settings import settings

from .loader import parse_manifest_file, parse_skill_md

_SLUG_RE = re.compile(r"^[a-z0-9-]{1,64}$")


def _github_to_raw(url: str) -> str:
    parsed = urllib.parse.urlparse(url)
    host = parsed.netloc.lower()
    if host == "raw.githubusercontent.com":
        return url
    if host not in {"github.com", "www.github.com"}:
        return url

    parts = [p for p in parsed.path.split("/") if p]
    if len(parts) < 5:
        return url

    owner, repo, marker, ref = parts[:4]
    rest = parts[4:]
    if marker not in {"blob", "tree"}:
        return url

    if marker == "tree" and (not rest or rest[-1] != "SKILL.md"):
        rest = [*rest, "SKILL.md"]

    raw_path = "/".join([owner, repo, ref, *rest])
    return urllib.parse.urlunparse(("https", "raw.githubusercontent.com", raw_path, "", "", ""))


def _download_text(url: str, timeout: int = 20) -> str:
    request = urllib.request.Request(
        _github_to_raw(url),
        headers={"User-Agent": "SARAH-AI-skill-installer/1.0"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            content_type = response.headers.get("content-type", "")
            if "text" not in content_type and "json" not in content_type and "octet-stream" not in content_type:
                raise ValueError(f"unexpected content type: {content_type}")
            return response.read().decode("utf-8")
    except urllib.error.URLError as exc:
        raise ValueError(f"failed to fetch skill: {exc}") from exc


def install_skill_from_url(
    url: str,
    *,
    workspace: Optional[Path] = None,
    overwrite: bool = False,
) -> Dict[str, object]:
    """Fetch a SKILL.md, write it under the project-local skills folder, validate it."""
    url = (url or "").strip()
    if not url:
        raise ValueError("url is required")

    text = _download_text(url)
    frontmatter, _body = parse_skill_md(text)
    if frontmatter is None:
        raise ValueError("downloaded file is not a valid SKILL.md")

    slug = (frontmatter.get("slug") or "").strip()
    if not _SLUG_RE.match(slug):
        raise ValueError("downloaded SKILL.md has an invalid or missing slug")

    if workspace is not None:
        root = workspace / "skills"
    else:
        root = settings.skills_path
    target_dir = root / slug
    target_path = target_dir / "SKILL.md"
    if target_path.exists() and not overwrite:
        raise FileExistsError(f"skill {slug!r} already exists; set overwrite=true")

    target_dir.mkdir(parents=True, exist_ok=True)
    target_path.write_text(text, encoding="utf-8")

    manifest = parse_manifest_file(target_path)
    if manifest is None:
        try:
            target_path.unlink()
        except OSError:
            pass
        raise ValueError("downloaded SKILL.md failed manifest validation")

    return {
        "slug": manifest["slug"],
        "name": manifest["name"],
        "description": manifest["description"],
        "path": str(target_path),
        "body_len": len(str(manifest.get("body") or "")),
        "source_url": url,
    }
