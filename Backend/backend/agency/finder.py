"""Finding anything on Zero's PC by name: apps, files, folders, projects.

Apps come from the Start menu (shortcuts, plus Store apps and games through
Get-StartApps) and desktop shortcuts; files and folders from the Windows
Search index (the whole indexed PC, fast), falling back to a time-boxed walk
of Zero's own folders; projects from Sarah's Projects list. Results are
ranked by how well their name matches what was asked, so "open my resume"
or "the Sarah project" finds the real thing.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import time
from pathlib import Path
from typing import Dict, Iterable, List, Optional

KINDS = ("any", "app", "file", "folder", "project")
_FILLER = {"my", "the", "a", "an", "called", "named", "file", "folder", "app", "application", "program",
           "project", "document", "doc", "please", "on", "in", "pc", "computer"}
_SKIP_DIRS = {"appdata", "node_modules", "__pycache__", ".git", ".venv", "venv", "site-packages", "$recycle.bin",
              "windows", "program files", "program files (x86)", "programdata"}
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
_apps_cache: Dict[str, object] = {"at": 0.0, "items": []}


def tokens(query: str) -> List[str]:
    words = re.findall(r"[a-z0-9][a-z0-9+#'&_-]*", (query or "").lower())
    kept = [w for w in words if w not in _FILLER]
    return kept or words


def rank(name: str, query: str) -> int:
    """0-100: how well an item's name matches the request."""
    words = tokens(query)
    if not words:
        return 0
    full = " ".join(words)
    label = (name or "").lower()
    stem = re.sub(r"\.(lnk|url|appref-ms|exe)$", "", label)
    bare = re.sub(r"\.[a-z0-9]{1,5}$", "", stem)
    plain = re.sub(r"[_\-.]+", " ", bare).strip()
    if full in (stem, bare, plain):
        return 100
    if plain.startswith(full) or bare.startswith(full):
        return 85
    parts = set(re.findall(r"[a-z0-9+#']+", plain))
    if all(w in parts for w in words):
        return 70 - min(15, max(0, len(parts) - len(words)) * 3)
    if all(w in plain for w in words):
        return 45
    return 0


def _item(name: str, kind: str, path: str, how: str = "path", modified: Optional[float] = None) -> Dict[str, object]:
    return {"name": name, "kind": kind, "path": path, "how": how, "modified": modified}


# ---------------------------------------------------------------------------
# Sources
# ---------------------------------------------------------------------------

def _start_menu_dirs() -> List[Path]:
    dirs = []
    for env in ("ProgramData", "APPDATA"):
        base = os.environ.get(env)
        if base:
            dirs.append(Path(base) / "Microsoft" / "Windows" / "Start Menu" / "Programs")
    from .desktop import known_folder

    desk = known_folder("desktop")
    if desk:
        dirs.append(desk)
    public = os.environ.get("PUBLIC")
    if public:
        dirs.append(Path(public) / "Desktop")
    return [d for d in dirs if d.exists()]


def apps(max_age: float = 600) -> List[Dict[str, object]]:
    """Every app Zero can start: Start menu and desktop shortcuts, Store apps."""
    if time.time() - float(_apps_cache["at"]) < max_age and _apps_cache["items"]:
        return list(_apps_cache["items"])  # type: ignore[arg-type]
    found: Dict[str, Dict[str, object]] = {}
    for root in _start_menu_dirs():
        for p in root.rglob("*"):
            if p.suffix.lower() in (".lnk", ".url", ".appref-ms") and not re.search(r"uninstall|readme|help|website", p.stem, re.I):
                found.setdefault(p.stem.lower(), _item(p.stem, "app", str(p)))
    if os.name == "nt":
        try:
            out = subprocess.run(["powershell", "-NoProfile", "-Command",
                                  "Get-StartApps | Select-Object Name, AppID | ConvertTo-Json -Compress"],
                                 capture_output=True, text=True, timeout=15, creationflags=_NO_WINDOW).stdout
            rows = json.loads(out or "[]")
            for row in rows if isinstance(rows, list) else [rows]:
                name, appid = row.get("Name"), row.get("AppID")
                if name and appid and name.lower() not in found:
                    found[name.lower()] = _item(name, "app", appid, how="appid")
        except Exception:
            pass
    _apps_cache.update(at=time.time(), items=list(found.values()))
    return list(found.values())


def projects() -> List[Dict[str, object]]:
    try:
        from backend.db import get_connection

        conn = get_connection()
        try:
            rows = conn.execute("SELECT name, root_path FROM projects WHERE root_path IS NOT NULL AND root_path != ''").fetchall()
        finally:
            conn.close()
        return [_item(r[0], "project", r[1]) for r in rows if r[1] and os.path.isdir(r[1])]
    except Exception:
        return []


def _search_index(words: List[str], kind: str, limit: int = 60) -> Optional[List[Dict[str, object]]]:
    """Windows Search over the whole index; None when it isn't available."""
    if os.name != "nt" or not words:
        return None
    # Only plain characters go into the query (no quotes or LIKE wildcards).
    safe = [w for w in (re.sub(r"[^a-z0-9+#&-]", "", w) for w in words[:6]) if w]
    if not safe:
        return None
    where = " AND ".join(f"System.FileName LIKE '%{w}%'" for w in safe)
    if kind == "folder":
        where += " AND System.ItemType = 'Directory'"
    elif kind == "file":
        where += " AND System.ItemType <> 'Directory'"
    sql = (f"SELECT TOP {int(limit)} System.ItemPathDisplay, System.ItemType, System.DateModified "
           f"FROM SystemIndex WHERE {where} ORDER BY System.DateModified DESC")
    script = (
        "$c = New-Object -ComObject ADODB.Connection;"
        "$c.Open(\"Provider=Search.CollatorDSO;Extended Properties='Application=Windows';\");"
        f"$r = $c.Execute(\"{sql}\");"
        "$o = @(); while (-not $r.EOF) { $o += [pscustomobject]@{p=$r.Fields.Item(0).Value; t=$r.Fields.Item(1).Value;"
        " m=[string]$r.Fields.Item(2).Value}; $r.MoveNext() }; $o | ConvertTo-Json -Compress"
    )
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-Command", script], capture_output=True, text=True,
                             timeout=12, creationflags=_NO_WINDOW)
        if out.returncode != 0:
            return None
        rows = json.loads(out.stdout or "[]")
    except Exception:
        return None
    items = []
    for row in rows if isinstance(rows, list) else [rows]:
        path = (row or {}).get("p")
        if not path:
            continue
        is_dir = str(row.get("t", "")).lower() == "directory"
        items.append(_item(Path(path).name, "folder" if is_dir else "file", path))
    return items


def _walk_roots() -> List[Path]:
    from .desktop import known_folder

    roots = [known_folder(n) for n in ("desktop", "documents", "downloads", "pictures", "music", "videos")]
    home = Path.home()
    roots += [p for p in home.glob("OneDrive*") if p.is_dir()]
    roots += [home / "source", home / "Projects", home]
    seen, out = set(), []
    for r in roots:
        if r and r.exists() and str(r).lower() not in seen:
            seen.add(str(r).lower())
            out.append(r)
    return out


def _walk(words: List[str], kind: str, roots: Optional[Iterable[Path]] = None, budget: float = 4.0,
          max_depth: int = 5) -> List[Dict[str, object]]:
    """Breadth-first over Zero's folders, stopping after `budget` seconds."""
    deadline = time.time() + budget
    queue = [(Path(r), 0) for r in (roots if roots is not None else _walk_roots())]
    seen, hits = set(), []
    while queue and time.time() < deadline:
        folder, depth = queue.pop(0)
        key = str(folder).lower()
        if key in seen:
            continue
        seen.add(key)
        try:
            entries = list(os.scandir(folder))
        except OSError:
            continue
        for e in entries:
            low = e.name.lower()
            try:
                is_dir = e.is_dir(follow_symlinks=False)
            except OSError:
                continue
            if all(w in low for w in words) and (kind == "any" or kind == ("folder" if is_dir else "file")):
                try:
                    modified = e.stat().st_mtime
                except OSError:
                    modified = None
                hits.append(_item(e.name, "folder" if is_dir else "file", e.path, modified=modified))
            if is_dir and depth < max_depth and not low.startswith(".") and low not in _SKIP_DIRS:
                queue.append((Path(e.path), depth + 1))
    return hits


# ---------------------------------------------------------------------------
# The public API
# ---------------------------------------------------------------------------

def find(query: str, kind: str = "any", limit: int = 8) -> List[Dict[str, object]]:
    """Best matches for `query`, best first. kind: any / app / file / folder / project."""
    kind = kind if kind in KINDS else "any"
    words = tokens(query)
    if not words:
        return []
    pool: List[Dict[str, object]] = []
    if kind in ("any", "app"):
        pool += apps()
    if kind in ("any", "project", "folder"):
        pool += projects()
    if kind in ("any", "file", "folder"):
        indexed = _search_index(words, kind)
        pool += indexed if indexed is not None else _walk(words, kind)
    scored = []
    seen = set()
    for it in pool:
        s = rank(str(it["name"]), query)
        key = str(it["path"]).lower()
        if s <= 0 or key in seen:
            continue
        seen.add(key)
        # Things you'd mean by a bare name first: projects, then apps, then folders, then files.
        bonus = {"project": 6, "app": 4, "folder": 2}.get(str(it["kind"]), 0)
        scored.append((s + bonus, it))
    scored.sort(key=lambda x: -x[0])
    return [{**it, "score": s} for s, it in scored[:max(1, limit)]]


def describe(items: List[Dict[str, object]]) -> str:
    if not items:
        return "Nothing on the PC matches that name."
    return "\n".join(f"{i + 1}. {it['name']} [{it['kind']}] {it['path']}" for i, it in enumerate(items))


def pick(items: List[Dict[str, object]]) -> Optional[Dict[str, object]]:
    """The one clear best match, or None when it's ambiguous or weak."""
    if not items or int(items[0]["score"]) < 60:
        return None
    if len(items) > 1 and int(items[0]["score"]) - int(items[1]["score"]) < 10 \
            and str(items[0]["name"]).lower() != str(items[1]["name"]).lower():
        return None
    return items[0]


def start(item: Dict[str, object]) -> None:
    """Open a found item the way double-clicking it would."""
    if item.get("how") == "appid":
        subprocess.Popen(["explorer.exe", f"shell:AppsFolder\\{item['path']}"], creationflags=_NO_WINDOW)
    else:
        os.startfile(str(item["path"]))  # type: ignore[attr-defined]
