"""Add skills to Sarah's own repertoire.

A skill (OpenClaw / Agent Skills format) is a folder with a SKILL.md
(frontmatter: name, description, optional slug/metadata) plus optional
scripts and reference files. Wherever it comes from, it is *copied into* her
repertoire (``settings.skills_path``, inside her workspace); she only ever
reads skills from there.

Sources: a GitHub folder or file URL (the whole skill folder is fetched,
scripts included), a raw SKILL.md URL, a .zip URL (e.g. from ClawHub), a
local folder, or a local .zip. A GitHub repo URL without a skill at its root
lists the skill folders it contains instead.
"""
from __future__ import annotations

import io
import json
import re
import shutil
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path
from typing import Dict, List, Optional

from backend.config.settings import settings

from .loader import parse_manifest_file, parse_skill_md

_SLUG_RE = re.compile(r"^[a-z0-9-]{1,64}$")
MAX_FILES = 120
MAX_BYTES = 8 * 1024 * 1024
_UA = {"User-Agent": "SARAH-AI-skill-installer/2.0"}


def _slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return slug[:64]


def _get(url: str, timeout: int = 30, accept: Optional[str] = None) -> bytes:
    headers = dict(_UA)
    if accept:
        headers["Accept"] = accept
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = response.read(MAX_BYTES + 1)
    except urllib.error.URLError as exc:
        raise ValueError(f"failed to fetch {url}: {exc}") from exc
    if len(data) > MAX_BYTES:
        raise ValueError("download is too large for a skill")
    return data


def _github_to_raw(url: str) -> str:
    parsed = urllib.parse.urlparse(url)
    host = parsed.netloc.lower()
    if host == "raw.githubusercontent.com":
        return url
    if host not in {"github.com", "www.github.com"}:
        return url
    parts = [p for p in parsed.path.split("/") if p]
    if len(parts) < 5 or parts[2] not in {"blob", "tree"}:
        return url
    owner, repo, marker, ref = parts[:4]
    rest = parts[4:]
    if marker == "tree" and (not rest or rest[-1] != "SKILL.md"):
        rest = [*rest, "SKILL.md"]
    return urllib.parse.urlunparse(("https", "raw.githubusercontent.com", "/".join([owner, repo, ref, *rest]), "", "", ""))


def _download_text(url: str, timeout: int = 20) -> str:
    return _get(_github_to_raw(url), timeout=timeout).decode("utf-8")


# ---------------------------------------------------------------------------
# Fetching a skill folder into a temporary directory
# ---------------------------------------------------------------------------

def _github_parts(url: str):
    parsed = urllib.parse.urlparse(url)
    if parsed.netloc.lower() not in {"github.com", "www.github.com"}:
        return None
    parts = [p for p in parsed.path.split("/") if p]
    if len(parts) < 2:
        return None
    owner, repo = parts[0], parts[1].removesuffix(".git")
    ref, path = None, ""
    if len(parts) >= 4 and parts[2] in {"tree", "blob"}:
        ref = parts[3]
        path = "/".join(parts[4:])
        if parts[2] == "blob":  # a file: take its folder (the skill)
            path = path.rsplit("/", 1)[0] if "/" in path else ""
    return owner, repo, ref, path


def _github_folder(owner: str, repo: str, ref: Optional[str], path: str, dest: Path) -> None:
    """Download a folder (recursively) via the GitHub contents API. A wrong
    path fails fast with a hint (the model may have guessed the folder)."""
    files = 0
    total = 0

    def walk(sub: str, into: Path) -> None:
        nonlocal files, total
        api = f"https://api.github.com/repos/{owner}/{repo}/contents/{urllib.parse.quote(sub)}"
        if ref:
            api += f"?ref={urllib.parse.quote(ref)}"
        listing = json.loads(_get(api, accept="application/vnd.github+json"))
        if isinstance(listing, dict):
            listing = [listing]
        into.mkdir(parents=True, exist_ok=True)
        for item in listing:
            if item.get("type") == "dir":
                walk(item["path"], into / item["name"])
            elif item.get("type") == "file" and item.get("download_url"):
                files += 1
                total += int(item.get("size") or 0)
                if files > MAX_FILES or total > MAX_BYTES:
                    raise ValueError("that folder is too big to be a skill")
                (into / item["name"]).write_bytes(_get(item["download_url"]))

    try:
        walk(path, dest)
    except ValueError as exc:
        if "404" in str(exc):
            raise ValueError(f"there's no folder '{path}' in {owner}/{repo}. add_skill "
                             f"https://github.com/{owner}/{repo} to get the exact skill URLs.") from exc
        raise


def _github_skill_folders(owner: str, repo: str, ref: Optional[str]) -> List[str]:
    """Folders in a repo that contain a SKILL.md (to choose from)."""
    branch = ref or json.loads(_get(f"https://api.github.com/repos/{owner}/{repo}"))["default_branch"]
    tree = json.loads(_get(f"https://api.github.com/repos/{owner}/{repo}/git/trees/{branch}?recursive=1"))
    return [
        f"https://github.com/{owner}/{repo}/tree/{branch}/{item['path'].rsplit('/', 1)[0]}"
        for item in tree.get("tree", [])
        if item.get("path", "").endswith("SKILL.md") and "/" in item["path"]
    ]


def _extract_zip(data: bytes, dest: Path) -> None:
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        members = [m for m in zf.infolist() if not m.is_dir()]
        if len(members) > MAX_FILES or sum(m.file_size for m in members) > MAX_BYTES:
            raise ValueError("that archive is too big to be a skill")
        for m in members:
            name = m.filename.replace("\\", "/")
            if name.startswith("/") or ".." in name.split("/"):
                continue  # never write outside the skill folder
            target = dest / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(zf.read(m))


def _find_skill_root(tmp: Path) -> Path:
    """The (shallowest) folder holding a SKILL.md."""
    candidates = sorted(tmp.rglob("SKILL.md"), key=lambda p: len(p.parts))
    if not candidates:
        raise ValueError("no SKILL.md found in that source")
    return candidates[0].parent


def _fetch(source: str, tmp: Path) -> Path:
    src = (source or "").strip().strip('"')
    local = Path(src).expanduser()
    if local.exists():
        if local.is_dir():
            # Keep the folder's own name: it names the skill if SKILL.md doesn't.
            shutil.copytree(local, tmp / (_slugify(local.name) or "skill"))
        elif local.suffix.lower() == ".zip":
            _extract_zip(local.read_bytes(), tmp)
        elif local.name.lower() == "skill.md" or local.suffix.lower() == ".md":
            (tmp / "skill").mkdir()
            shutil.copy2(local, tmp / "skill" / "SKILL.md")
        else:
            raise ValueError("a local skill must be a folder, a .zip or a SKILL.md")
        return _find_skill_root(tmp)
    if not re.match(r"^https?://", src):
        raise ValueError("give a URL (GitHub, a SKILL.md link, a .zip) or a local folder/zip path")
    gh = _github_parts(src)
    if gh:
        owner, repo, ref, path = gh
        if not path:
            # A whole repository: never download all of it. If its top level
            # isn't itself a skill, list the skills it contains to choose from.
            api = f"https://api.github.com/repos/{owner}/{repo}/contents/" + (f"?ref={urllib.parse.quote(ref)}" if ref else "")
            top = json.loads(_get(api, accept="application/vnd.github+json"))
            if not any(item.get("name") == "SKILL.md" for item in top if isinstance(item, dict)):
                folders = _github_skill_folders(owner, repo, ref)
                if not folders:
                    raise ValueError("that repository has no SKILL.md anywhere")
                raise LookupError("This repository holds several skills; add_skill one of these exact URLs: "
                                  + ", ".join(folders[:60]))
        dest = tmp / (_slugify(path.rstrip("/").split("/")[-1]) or "skill")
        _github_folder(owner, repo, ref, path, dest)
        if not (dest / "SKILL.md").exists():
            raise ValueError("no SKILL.md in that folder")
        return dest
    data = _get(src)
    if data[:2] == b"PK":
        _extract_zip(data, tmp)
        return _find_skill_root(tmp)
    (tmp / "skill").mkdir()
    (tmp / "skill" / "SKILL.md").write_bytes(data)
    return tmp / "skill"


# ---------------------------------------------------------------------------
# Installing
# ---------------------------------------------------------------------------

def install_skill(source: str, *, workspace: Optional[Path] = None, overwrite: bool = False) -> Dict[str, object]:
    """Copy a skill from `source` into her repertoire and validate it."""
    root = (workspace / "skills") if workspace is not None else settings.skills_path
    root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="sarah_skill_") as tmpdir:
        folder = _fetch(source, Path(tmpdir))
        text = (folder / "SKILL.md").read_text(encoding="utf-8", errors="replace")
        frontmatter, _body = parse_skill_md(text)
        if frontmatter is None:
            raise ValueError("that SKILL.md has no valid frontmatter")
        slug = (frontmatter.get("slug") or "").strip()
        if slug and not _SLUG_RE.match(slug):
            raise ValueError(f"invalid slug {slug!r}")
        slug = slug or _slugify(frontmatter.get("name", "")) or _slugify(folder.name)
        if not slug:
            raise ValueError("couldn't work out a name for this skill")
        target = root / slug
        if target.exists():
            if not overwrite:
                raise FileExistsError(f"skill {slug!r} is already in the repertoire; set overwrite=true")
            shutil.rmtree(target)
        shutil.copytree(folder, target)
    manifest = parse_manifest_file(target / "SKILL.md")
    if manifest is None:
        shutil.rmtree(target, ignore_errors=True)
        raise ValueError("the skill failed validation (needs name and description)")
    files = sorted(str(p.relative_to(target)).replace("\\", "/") for p in target.rglob("*") if p.is_file())
    return {
        "slug": manifest["slug"],
        "name": manifest["name"],
        "description": manifest["description"],
        "path": str(target / "SKILL.md"),
        "folder": str(target),
        "files": files,
        "body_len": len(str(manifest.get("body") or "")),
        "source_url": source,
    }


def install_skill_from_url(url: str, *, workspace: Optional[Path] = None, overwrite: bool = False) -> Dict[str, object]:
    """Back-compat entry (API): a single SKILL.md URL, a GitHub link or a zip."""
    url = (url or "").strip()
    if not url:
        raise ValueError("url is required")
    parsed = urllib.parse.urlparse(url)
    if parsed.netloc.lower() in {"github.com", "www.github.com"}:
        return install_skill(url, workspace=workspace, overwrite=overwrite)
    text = _download_text(url)
    frontmatter, _ = parse_skill_md(text)
    if frontmatter is None:
        raise ValueError("downloaded file is not a valid SKILL.md")
    with tempfile.TemporaryDirectory(prefix="sarah_skill_") as tmpdir:
        folder = Path(tmpdir) / "skill"
        folder.mkdir()
        (folder / "SKILL.md").write_text(text, encoding="utf-8")
        return install_skill(str(folder), workspace=workspace, overwrite=overwrite)
