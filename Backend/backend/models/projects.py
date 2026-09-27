# backend/models/projects.py
"""
SQL functions for managing projects and their files.
Enhanced with git integration, file tree, and metadata detection.
"""
import json
import os
import subprocess
import sqlite3
from pathlib import Path
from typing import Optional, List, Dict, Any
from backend.db import get_connection


def create_project(
    name: str,
    description: Optional[str] = None,
    root_path: Optional[str] = None
) -> int:
    """Create a new project and return its ID."""
    conn = get_connection()
    cur = conn.cursor()

    # Detect metadata if root_path is provided
    metadata = {}
    git_repo_path = None
    current_branch = None
    primary_language = None
    framework = None
    package_manager = None

    if root_path and os.path.exists(root_path):
        metadata = detect_project_metadata(root_path)
        git_info = detect_git_info(root_path)

        git_repo_path = git_info.get("repo_path")
        current_branch = git_info.get("current_branch")
        primary_language = metadata.get("primary_language")
        framework = metadata.get("framework")
        package_manager = metadata.get("package_manager")

    cur.execute(
        """
        INSERT INTO projects (
            name, description, root_path, git_repo_path, current_branch,
            primary_language, framework, package_manager, metadata_json
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            name,
            description or "",
            root_path,
            git_repo_path,
            current_branch,
            primary_language,
            framework,
            package_manager,
            json.dumps(metadata)
        )
    )

    project_id = cur.lastrowid
    conn.commit()
    conn.close()
    return project_id


def list_projects(limit: int = 100) -> List[Dict[str, Any]]:
    """List all projects with file counts and metadata."""
    conn = get_connection()
    cur = conn.cursor()

    cur.execute(
        """
        SELECT
            p.id,
            p.name,
            p.description,
            p.root_path,
            p.git_repo_path,
            p.current_branch,
            p.primary_language,
            p.framework,
            p.package_manager,
            p.created_at,
            p.last_accessed_at,
            COUNT(pf.id) as file_count,
            COALESCE(SUM(pf.file_size), 0) as total_size
        FROM projects p
        LEFT JOIN project_files pf ON p.id = pf.project_id
        GROUP BY p.id
        ORDER BY p.last_accessed_at DESC
        LIMIT ?
        """,
        (limit,)
    )

    projects = []
    for row in cur.fetchall():
        projects.append({
            "id": row["id"],
            "name": row["name"],
            "description": row["description"],
            "root_path": row["root_path"],
            "git_repo_path": row["git_repo_path"],
            "current_branch": row["current_branch"],
            "primary_language": row["primary_language"],
            "framework": row["framework"],
            "package_manager": row["package_manager"],
            "created_at": row["created_at"],
            "last_accessed_at": row["last_accessed_at"],
            "file_count": row["file_count"],
            "total_size": row["total_size"]
        })

    conn.close()
    return projects


def get_project(project_id: int) -> Optional[Dict[str, Any]]:
    """Get a single project by ID with total size."""
    conn = get_connection()
    cur = conn.cursor()

    cur.execute(
        """
        SELECT
            p.id,
            p.name,
            p.description,
            p.created_at,
            p.last_accessed_at,
            COUNT(pf.id) as file_count,
            COALESCE(SUM(pf.file_size), 0) as total_size
        FROM projects p
        LEFT JOIN project_files pf ON p.id = pf.project_id
        WHERE p.id = ?
        GROUP BY p.id
        """,
        (project_id,)
    )

    row = cur.fetchone()
    conn.close()

    if not row:
        return None

    return {
        "id": row["id"],
        "name": row["name"],
        "description": row["description"],
        "created_at": row["created_at"],
        "last_accessed_at": row["last_accessed_at"],
        "file_count": row["file_count"],
        "total_size": row["total_size"]
    }


def delete_project(project_id: int):
    """Delete a project and all its files."""
    conn = get_connection()
    cur = conn.cursor()

    cur.execute("DELETE FROM projects WHERE id = ?", (project_id,))

    conn.commit()
    conn.close()


def rename_project(project_id: int, new_name: str) -> bool:
    """Rename a project."""
    conn = get_connection()
    cur = conn.cursor()

    cur.execute(
        "UPDATE projects SET name = ?, last_accessed_at = datetime('now') WHERE id = ?",
        (new_name, project_id)
    )

    success = cur.rowcount > 0
    conn.commit()
    conn.close()
    return success


def add_file_to_project(
    project_id: int,
    file_name: str,
    file_path: str,
    content: str,
    file_type: Optional[str] = None,
    file_size: Optional[int] = None
) -> int:
    """Add a file to a project."""
    conn = get_connection()
    cur = conn.cursor()

    cur.execute(
        """
        INSERT INTO project_files (project_id, file_name, file_path, content, file_type, file_size)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (project_id, file_name, file_path, content, file_type or "", file_size or 0)
    )

    file_id = cur.lastrowid

    # Update project's last_accessed_at
    cur.execute(
        """
        UPDATE projects
        SET last_accessed_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (project_id,)
    )

    conn.commit()
    conn.close()
    return file_id


def get_project_files(project_id: int) -> List[Dict[str, Any]]:
    """Get all files in a project."""
    conn = get_connection()
    cur = conn.cursor()

    cur.execute(
        """
        SELECT id, project_id, file_name, file_path, file_type, file_size, created_at
        FROM project_files
        WHERE project_id = ?
        ORDER BY created_at DESC
        """,
        (project_id,)
    )

    files = []
    for row in cur.fetchall():
        files.append({
            "id": row["id"],
            "project_id": row["project_id"],
            "file_name": row["file_name"],
            "file_path": row["file_path"],
            "file_type": row["file_type"],
            "file_size": row["file_size"],
            "created_at": row["created_at"]
        })

    conn.close()
    return files


def get_file_content(file_id: int) -> Optional[str]:
    """Get the content of a specific file."""
    conn = get_connection()
    cur = conn.cursor()

    cur.execute(
        """
        SELECT content
        FROM project_files
        WHERE id = ?
        """,
        (file_id,)
    )

    row = cur.fetchone()
    conn.close()

    if not row:
        return None

    return row["content"]


def delete_file(file_id: int):
    """Delete a file from a project."""
    conn = get_connection()
    cur = conn.cursor()

    cur.execute("DELETE FROM project_files WHERE id = ?", (file_id,))

    conn.commit()
    conn.close()


def get_project_context(project_id: int, max_files: int = 50) -> str:
    """
    Generate a text context summary of all files in a project.
    This can be passed to the AI for context understanding.
    """
    conn = get_connection()
    cur = conn.cursor()

    # Get project info
    cur.execute(
        """
        SELECT name, description
        FROM projects
        WHERE id = ?
        """,
        (project_id,)
    )
    project_row = cur.fetchone()

    if not project_row:
        conn.close()
        return ""

    # Get files with content
    cur.execute(
        """
        SELECT file_name, file_path, file_type, content
        FROM project_files
        WHERE project_id = ?
        ORDER BY created_at ASC
        LIMIT ?
        """,
        (project_id, max_files)
    )

    context_parts = [
        f"Project: {project_row['name']}",
        f"Description: {project_row['description'] or 'No description'}",
        "",
        "Files:",
        ""
    ]

    for row in cur.fetchall():
        context_parts.append(f"--- {row['file_name']} ({row['file_type'] or 'unknown type'}) ---")
        context_parts.append(f"Path: {row['file_path']}")
        context_parts.append(f"Content:")
        context_parts.append(row['content'] or "(empty)")
        context_parts.append("")

    conn.close()
    return "\n".join(context_parts)


# -----------------------------------------------------------------------------
# Git Integration Functions
# -----------------------------------------------------------------------------

def detect_git_info(path: str) -> Dict[str, Any]:
    """Detect git repository information."""
    result = {
        "repo_path": None,
        "current_branch": None,
        "is_git_repo": False
    }

    try:
        # Check if path is in a git repository
        git_check = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=path,
            capture_output=True,
            text=True,
            timeout=5
        )

        if git_check.returncode == 0:
            result["is_git_repo"] = True
            result["repo_path"] = git_check.stdout.strip()

            # Get current branch
            branch_check = subprocess.run(
                ["git", "branch", "--show-current"],
                cwd=path,
                capture_output=True,
                text=True,
                timeout=5
            )
            if branch_check.returncode == 0:
                result["current_branch"] = branch_check.stdout.strip()

    except Exception as e:
        print(f"[Git Detection] Error: {e}")

    return result


def get_git_status(project_id: int) -> Dict[str, Any]:
    """Get git status for a project."""
    conn = get_connection()
    cur = conn.cursor()

    cur.execute("SELECT git_repo_path FROM projects WHERE id = ?", (project_id,))
    row = cur.fetchone()
    conn.close()

    if not row or not row["git_repo_path"]:
        return {"ok": False, "error": "Not a git repository"}

    repo_path = row["git_repo_path"]

    try:
        # Get status
        status_result = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=repo_path,
            capture_output=True,
            text=True,
            timeout=5
        )

        # Get current branch
        branch_result = subprocess.run(
            ["git", "branch", "--show-current"],
            cwd=repo_path,
            capture_output=True,
            text=True,
            timeout=5
        )

        # Parse status
        modified = []
        added = []
        deleted = []
        untracked = []

        for line in status_result.stdout.strip().split("\n"):
            if not line:
                continue
            status_code = line[:2]
            file_path = line[3:]

            if status_code.strip() == "M":
                modified.append(file_path)
            elif status_code.strip() == "A":
                added.append(file_path)
            elif status_code.strip() == "D":
                deleted.append(file_path)
            elif status_code.strip() == "??":
                untracked.append(file_path)

        return {
            "ok": True,
            "branch": branch_result.stdout.strip(),
            "modified": modified,
            "added": added,
            "deleted": deleted,
            "untracked": untracked
        }

    except Exception as e:
        return {"ok": False, "error": str(e)}


def get_recent_commits(project_id: int, limit: int = 10) -> List[Dict[str, Any]]:
    """Get recent git commits for a project."""
    conn = get_connection()
    cur = conn.cursor()

    cur.execute("SELECT git_repo_path FROM projects WHERE id = ?", (project_id,))
    row = cur.fetchone()
    conn.close()

    if not row or not row["git_repo_path"]:
        return []

    repo_path = row["git_repo_path"]

    try:
        # Get commits with format: hash|author|timestamp|message
        result = subprocess.run(
            [
                "git", "log",
                f"-{limit}",
                "--pretty=format:%H|%an|%at|%s"
            ],
            cwd=repo_path,
            capture_output=True,
            text=True,
            timeout=10
        )

        commits = []
        for line in result.stdout.strip().split("\n"):
            if not line:
                continue

            parts = line.split("|", 3)
            if len(parts) == 4:
                commits.append({
                    "hash": parts[0],
                    "author": parts[1],
                    "timestamp": int(parts[2]),
                    "message": parts[3]
                })

        return commits

    except Exception as e:
        print(f"[Git Commits] Error: {e}")
        return []


def get_git_diff(project_id: int, file_path: Optional[str] = None) -> str:
    """Get git diff for a project or specific file."""
    conn = get_connection()
    cur = conn.cursor()

    cur.execute("SELECT git_repo_path FROM projects WHERE id = ?", (project_id,))
    row = cur.fetchone()
    conn.close()

    if not row or not row["git_repo_path"]:
        return ""

    repo_path = row["git_repo_path"]

    try:
        cmd = ["git", "diff"]
        if file_path:
            cmd.append(file_path)

        result = subprocess.run(
            cmd,
            cwd=repo_path,
            capture_output=True,
            text=True,
            timeout=10
        )

        return result.stdout

    except Exception as e:
        return f"Error: {e}"


def git_add(project_id: int, files: Optional[List[str]] = None) -> Dict[str, Any]:
    """Stage files for commit. If files is None, stages all changes."""
    conn = get_connection()
    cur = conn.cursor()

    cur.execute("SELECT git_repo_path FROM projects WHERE id = ?", (project_id,))
    row = cur.fetchone()
    conn.close()

    if not row or not row["git_repo_path"]:
        return {"ok": False, "error": "Not a git repository"}

    repo_path = row["git_repo_path"]

    try:
        if files:
            cmd = ["git", "add"] + files
        else:
            cmd = ["git", "add", "-A"]

        result = subprocess.run(
            cmd,
            cwd=repo_path,
            capture_output=True,
            text=True,
            timeout=30
        )

        if result.returncode == 0:
            return {"ok": True, "message": "Files staged successfully"}
        else:
            return {"ok": False, "error": result.stderr.strip()}

    except Exception as e:
        return {"ok": False, "error": str(e)}


def git_commit(project_id: int, message: str) -> Dict[str, Any]:
    """Commit staged changes with the given message."""
    conn = get_connection()
    cur = conn.cursor()

    cur.execute("SELECT git_repo_path FROM projects WHERE id = ?", (project_id,))
    row = cur.fetchone()
    conn.close()

    if not row or not row["git_repo_path"]:
        return {"ok": False, "error": "Not a git repository"}

    repo_path = row["git_repo_path"]

    try:
        result = subprocess.run(
            ["git", "commit", "-m", message],
            cwd=repo_path,
            capture_output=True,
            text=True,
            timeout=30
        )

        if result.returncode == 0:
            # Extract commit hash from output
            output = result.stdout.strip()
            return {"ok": True, "message": "Commit successful", "output": output}
        else:
            error = result.stderr.strip()
            if "nothing to commit" in error.lower() or "nothing to commit" in result.stdout.lower():
                return {"ok": False, "error": "Nothing to commit - no staged changes"}
            return {"ok": False, "error": error}

    except Exception as e:
        return {"ok": False, "error": str(e)}


def git_push(project_id: int, remote: str = "origin", branch: Optional[str] = None) -> Dict[str, Any]:
    """Push commits to remote repository."""
    conn = get_connection()
    cur = conn.cursor()

    cur.execute("SELECT git_repo_path, current_branch FROM projects WHERE id = ?", (project_id,))
    row = cur.fetchone()
    conn.close()

    if not row or not row["git_repo_path"]:
        return {"ok": False, "error": "Not a git repository"}

    repo_path = row["git_repo_path"]
    target_branch = branch or row["current_branch"] or "main"

    try:
        result = subprocess.run(
            ["git", "push", remote, target_branch],
            cwd=repo_path,
            capture_output=True,
            text=True,
            timeout=60
        )

        if result.returncode == 0:
            return {"ok": True, "message": f"Pushed to {remote}/{target_branch}", "output": result.stdout.strip()}
        else:
            return {"ok": False, "error": result.stderr.strip()}

    except Exception as e:
        return {"ok": False, "error": str(e)}


def git_pull(project_id: int, remote: str = "origin", branch: Optional[str] = None) -> Dict[str, Any]:
    """Pull changes from remote repository."""
    conn = get_connection()
    cur = conn.cursor()

    cur.execute("SELECT git_repo_path, current_branch FROM projects WHERE id = ?", (project_id,))
    row = cur.fetchone()
    conn.close()

    if not row or not row["git_repo_path"]:
        return {"ok": False, "error": "Not a git repository"}

    repo_path = row["git_repo_path"]
    target_branch = branch or row["current_branch"] or "main"

    try:
        result = subprocess.run(
            ["git", "pull", remote, target_branch],
            cwd=repo_path,
            capture_output=True,
            text=True,
            timeout=60
        )

        if result.returncode == 0:
            output = result.stdout.strip()
            if "Already up to date" in output:
                return {"ok": True, "message": "Already up to date", "output": output}
            return {"ok": True, "message": f"Pulled from {remote}/{target_branch}", "output": output}
        else:
            return {"ok": False, "error": result.stderr.strip()}

    except Exception as e:
        return {"ok": False, "error": str(e)}


def git_stash(project_id: int, action: str = "push", message: Optional[str] = None) -> Dict[str, Any]:
    """Stash or unstash changes. action can be 'push', 'pop', 'list', 'clear'."""
    conn = get_connection()
    cur = conn.cursor()

    cur.execute("SELECT git_repo_path FROM projects WHERE id = ?", (project_id,))
    row = cur.fetchone()
    conn.close()

    if not row or not row["git_repo_path"]:
        return {"ok": False, "error": "Not a git repository"}

    repo_path = row["git_repo_path"]

    try:
        if action == "push":
            cmd = ["git", "stash", "push"]
            if message:
                cmd.extend(["-m", message])
        elif action == "pop":
            cmd = ["git", "stash", "pop"]
        elif action == "list":
            cmd = ["git", "stash", "list"]
        elif action == "clear":
            cmd = ["git", "stash", "clear"]
        else:
            return {"ok": False, "error": f"Unknown stash action: {action}"}

        result = subprocess.run(
            cmd,
            cwd=repo_path,
            capture_output=True,
            text=True,
            timeout=30
        )

        if result.returncode == 0:
            return {"ok": True, "message": f"Stash {action} successful", "output": result.stdout.strip()}
        else:
            return {"ok": False, "error": result.stderr.strip() or "No stash entries found"}

    except Exception as e:
        return {"ok": False, "error": str(e)}


def git_branch_list(project_id: int) -> Dict[str, Any]:
    """List all branches."""
    conn = get_connection()
    cur = conn.cursor()

    cur.execute("SELECT git_repo_path FROM projects WHERE id = ?", (project_id,))
    row = cur.fetchone()
    conn.close()

    if not row or not row["git_repo_path"]:
        return {"ok": False, "error": "Not a git repository"}

    repo_path = row["git_repo_path"]

    try:
        result = subprocess.run(
            ["git", "branch", "-a"],
            cwd=repo_path,
            capture_output=True,
            text=True,
            timeout=10
        )

        if result.returncode == 0:
            branches = []
            current = None
            for line in result.stdout.strip().split("\n"):
                line = line.strip()
                if line.startswith("*"):
                    current = line[2:].strip()
                    branches.append(current)
                elif line:
                    branches.append(line)
            return {"ok": True, "branches": branches, "current": current}
        else:
            return {"ok": False, "error": result.stderr.strip()}

    except Exception as e:
        return {"ok": False, "error": str(e)}


def git_checkout(project_id: int, branch: str, create: bool = False) -> Dict[str, Any]:
    """Checkout a branch. If create=True, creates a new branch."""
    conn = get_connection()
    cur = conn.cursor()

    cur.execute("SELECT git_repo_path FROM projects WHERE id = ?", (project_id,))
    row = cur.fetchone()
    conn.close()

    if not row or not row["git_repo_path"]:
        return {"ok": False, "error": "Not a git repository"}

    repo_path = row["git_repo_path"]

    try:
        if create:
            cmd = ["git", "checkout", "-b", branch]
        else:
            cmd = ["git", "checkout", branch]

        result = subprocess.run(
            cmd,
            cwd=repo_path,
            capture_output=True,
            text=True,
            timeout=30
        )

        if result.returncode == 0:
            # Update the current_branch in the database
            conn = get_connection()
            cur = conn.cursor()
            cur.execute(
                "UPDATE projects SET current_branch = ? WHERE id = ?",
                (branch, project_id)
            )
            conn.commit()
            conn.close()

            action = "Created and switched to" if create else "Switched to"
            return {"ok": True, "message": f"{action} branch '{branch}'"}
        else:
            return {"ok": False, "error": result.stderr.strip()}

    except Exception as e:
        return {"ok": False, "error": str(e)}


# -----------------------------------------------------------------------------
# Project Metadata Detection
# -----------------------------------------------------------------------------

def detect_project_metadata(root_path: str) -> Dict[str, Any]:
    """Detect project metadata like language, framework, dependencies."""
    metadata = {
        "primary_language": None,
        "framework": None,
        "package_manager": None,
        "dependencies": [],
        "config_files": []
    }

    try:
        files = os.listdir(root_path)

        # Python detection
        if "requirements.txt" in files or "setup.py" in files or "pyproject.toml" in files:
            metadata["primary_language"] = "Python"
            if "requirements.txt" in files:
                metadata["config_files"].append("requirements.txt")
                metadata["package_manager"] = "pip"
            if "pyproject.toml" in files:
                metadata["config_files"].append("pyproject.toml")
                metadata["package_manager"] = "poetry/pip"

            # Detect Python frameworks
            if "manage.py" in files:
                metadata["framework"] = "Django"
            elif "app.py" in files or "main.py" in files:
                req_path = os.path.join(root_path, "requirements.txt")
                if os.path.exists(req_path):
                    with open(req_path, "r", encoding="utf-8") as f:
                        content = f.read().lower()
                        if "flask" in content:
                            metadata["framework"] = "Flask"
                        elif "fastapi" in content:
                            metadata["framework"] = "FastAPI"

        # JavaScript/TypeScript detection
        elif "package.json" in files:
            metadata["primary_language"] = "JavaScript/TypeScript"
            metadata["config_files"].append("package.json")
            metadata["package_manager"] = "npm"

            if "yarn.lock" in files:
                metadata["package_manager"] = "yarn"
            elif "pnpm-lock.yaml" in files:
                metadata["package_manager"] = "pnpm"

            # Detect JS frameworks
            pkg_path = os.path.join(root_path, "package.json")
            try:
                with open(pkg_path, "r", encoding="utf-8") as f:
                    pkg_data = json.load(f)
                    deps = {**pkg_data.get("dependencies", {}), **pkg_data.get("devDependencies", {})}

                    if "react" in deps:
                        metadata["framework"] = "React"
                        if "next" in deps:
                            metadata["framework"] = "Next.js"
                    elif "vue" in deps:
                        metadata["framework"] = "Vue.js"
                    elif "angular" in deps or "@angular/core" in deps:
                        metadata["framework"] = "Angular"
                    elif "svelte" in deps:
                        metadata["framework"] = "Svelte"
            except Exception:
                pass

        # Rust detection
        elif "Cargo.toml" in files:
            metadata["primary_language"] = "Rust"
            metadata["config_files"].append("Cargo.toml")
            metadata["package_manager"] = "cargo"

        # Go detection
        elif "go.mod" in files:
            metadata["primary_language"] = "Go"
            metadata["config_files"].append("go.mod")
            metadata["package_manager"] = "go modules"

        # Java detection
        elif "pom.xml" in files:
            metadata["primary_language"] = "Java"
            metadata["config_files"].append("pom.xml")
            metadata["package_manager"] = "Maven"
            metadata["framework"] = "Maven"
        elif "build.gradle" in files or "build.gradle.kts" in files:
            metadata["primary_language"] = "Java/Kotlin"
            metadata["config_files"].append("build.gradle")
            metadata["package_manager"] = "Gradle"
            metadata["framework"] = "Gradle"

        # C# detection
        elif any(f.endswith(".csproj") for f in files):
            metadata["primary_language"] = "C#"
            metadata["package_manager"] = "NuGet"
            metadata["framework"] = ".NET"

    except Exception as e:
        print(f"[Metadata Detection] Error: {e}")

    return metadata


def build_file_tree(root_path: str, max_depth: int = 5, ignore_patterns: Optional[List[str]] = None) -> Dict[str, Any]:
    """Build a file tree structure for visualization."""
    if ignore_patterns is None:
        ignore_patterns = [
            "node_modules", ".git", "__pycache__", ".venv", "venv",
            "dist", "build", ".next", ".cache", "target"
        ]

    def should_ignore(path: str) -> bool:
        path_parts = Path(path).parts
        return any(pattern in path_parts for pattern in ignore_patterns)

    def build_tree(path: str, depth: int = 0) -> Dict[str, Any]:
        if depth > max_depth:
            return None

        try:
            path_obj = Path(path)
            result = {
                "name": path_obj.name,
                "path": str(path_obj),
                "type": "directory" if path_obj.is_dir() else "file",
                "children": []
            }

            if path_obj.is_dir():
                try:
                    for item in sorted(path_obj.iterdir()):
                        if should_ignore(str(item)):
                            continue

                        child = build_tree(str(item), depth + 1)
                        if child:
                            result["children"].append(child)
                except PermissionError:
                    pass

            return result

        except Exception as e:
            print(f"[File Tree] Error at {path}: {e}")
            return None

    return build_tree(root_path) or {}


def set_current_file(project_id: int, file_id: int):
    """Mark a file as the current working file."""
    conn = get_connection()
    cur = conn.cursor()

    # Clear previous current file
    cur.execute(
        "UPDATE project_files SET is_current_file = 0 WHERE project_id = ?",
        (project_id,)
    )

    # Set new current file
    cur.execute(
        "UPDATE project_files SET is_current_file = 1 WHERE id = ?",
        (file_id,)
    )

    conn.commit()
    conn.close()


def get_current_file(project_id: int) -> Optional[Dict[str, Any]]:
    """Get the current working file for a project."""
    conn = get_connection()
    cur = conn.cursor()

    cur.execute(
        """
        SELECT id, file_name, file_path, file_type, content
        FROM project_files
        WHERE project_id = ? AND is_current_file = 1
        LIMIT 1
        """,
        (project_id,)
    )

    row = cur.fetchone()
    conn.close()

    if not row:
        return None

    return {
        "id": row["id"],
        "file_name": row["file_name"],
        "file_path": row["file_path"],
        "file_type": row["file_type"],
        "content": row["content"]
    }


# -----------------------------------------------------------------------------
# Enhanced File Management Functions
# -----------------------------------------------------------------------------

def create_file_in_project(
    project_id: int,
    file_name: str,
    content: str = "",
    file_path: Optional[str] = None
) -> int:
    """Create a new file directly in a project."""
    conn = get_connection()
    cur = conn.cursor()

    if not file_path:
        file_path = file_name

    # Detect file type from extension
    file_type = "text/plain"
    ext = os.path.splitext(file_name)[1].lower()

    type_map = {
        ".py": "text/x-python",
        ".js": "text/javascript",
        ".ts": "text/typescript",
        ".html": "text/html",
        ".css": "text/css",
        ".json": "application/json",
        ".md": "text/markdown",
        ".txt": "text/plain",
        ".rs": "text/x-rust",
        ".go": "text/x-go",
        ".java": "text/x-java",
    }
    file_type = type_map.get(ext, "text/plain")

    file_size = len(content.encode('utf-8'))

    cur.execute(
        """
        INSERT INTO project_files
        (project_id, file_name, file_path, content, file_type, file_size, last_modified)
        VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        """,
        (project_id, file_name, file_path, content, file_type, file_size)
    )

    file_id = cur.lastrowid

    # Update project last_accessed_at
    cur.execute(
        "UPDATE projects SET last_accessed_at = CURRENT_TIMESTAMP WHERE id = ?",
        (project_id,)
    )

    conn.commit()
    conn.close()
    return file_id


def update_file_content(file_id: int, content: str):
    """Update the content of an existing file."""
    conn = get_connection()
    cur = conn.cursor()

    file_size = len(content.encode('utf-8'))

    cur.execute(
        """
        UPDATE project_files
        SET content = ?, file_size = ?, last_modified = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (content, file_size, file_id)
    )

    conn.commit()
    conn.close()


def rename_file(file_id: int, new_name: str):
    """Rename a file in the project."""
    conn = get_connection()
    cur = conn.cursor()

    # Get current file info
    cur.execute("SELECT file_path FROM project_files WHERE id = ?", (file_id,))
    row = cur.fetchone()

    if row:
        old_path = row["file_path"]
        # Update path to use new name
        path_parts = old_path.rsplit("/", 1) if "/" in old_path else old_path.rsplit("\\", 1)
        if len(path_parts) > 1:
            new_path = f"{path_parts[0]}/{new_name}"
        else:
            new_path = new_name

        cur.execute(
            """
            UPDATE project_files
            SET file_name = ?, file_path = ?, last_modified = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (new_name, new_path, file_id)
        )

    conn.commit()
    conn.close()


# -----------------------------------------------------------------------------
# Project-Conversation Association Functions
# -----------------------------------------------------------------------------

def tag_conversation_to_project(project_id: int, conversation_id: int):
    """Associate a conversation with a project."""
    conn = get_connection()
    cur = conn.cursor()

    try:
        cur.execute(
            """
            INSERT INTO project_conversations (project_id, conversation_id)
            VALUES (?, ?)
            """,
            (project_id, conversation_id)
        )
        conn.commit()
    except sqlite3.IntegrityError:
        # Already tagged
        pass
    finally:
        conn.close()


def untag_conversation_from_project(project_id: int, conversation_id: int):
    """Remove conversation-project association."""
    conn = get_connection()
    cur = conn.cursor()

    cur.execute(
        """
        DELETE FROM project_conversations
        WHERE project_id = ? AND conversation_id = ?
        """,
        (project_id, conversation_id)
    )

    conn.commit()
    conn.close()


def get_project_conversations(project_id: int) -> List[Dict[str, Any]]:
    """Get all conversations tagged to a project."""
    conn = get_connection()
    cur = conn.cursor()

    cur.execute(
        """
        SELECT c.id, c.title, c.created_at, c.last_active_at
        FROM conversations c
        INNER JOIN project_conversations pc ON c.id = pc.conversation_id
        WHERE pc.project_id = ?
        ORDER BY c.last_active_at DESC
        """,
        (project_id,)
    )

    conversations = []
    for row in cur.fetchall():
        conversations.append({
            "id": row["id"],
            "title": row["title"],
            "created_at": row["created_at"],
            "last_active_at": row["last_active_at"]
        })

    conn.close()
    return conversations


def get_conversation_projects(conversation_id: int) -> List[Dict[str, Any]]:
    """Get all projects a conversation is tagged to."""
    conn = get_connection()
    cur = conn.cursor()

    cur.execute(
        """
        SELECT p.id, p.name, p.description
        FROM projects p
        INNER JOIN project_conversations pc ON p.id = pc.project_id
        WHERE pc.conversation_id = ?
        ORDER BY p.name
        """,
        (conversation_id,)
    )

    projects = []
    for row in cur.fetchall():
        projects.append({
            "id": row["id"],
            "name": row["name"],
            "description": row["description"]
        })

    conn.close()
    return projects
