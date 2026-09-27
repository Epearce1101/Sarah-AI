# backend/project_analysis.py
"""
Comprehensive project analysis system for SARAH AI.
Provides deep understanding of projects including:
- File structure and dependency analysis
- README content extraction
- Git history and recent changes
- Entry point detection
- Project structure understanding
"""

import json
import os
import subprocess
from pathlib import Path
from typing import Optional, List, Dict, Any

from backend.db import get_connection
from backend.models.projects import build_file_tree


def analyze_project_full(project_id: int) -> Dict[str, Any]:
    """
    Perform comprehensive project analysis including:
    - File structure and tree
    - Dependencies (package.json, requirements.txt, etc.)
    - README content
    - Git history and recent changes
    - Entry points detection
    - Project understanding and direction
    """
    conn = get_connection()
    cur = conn.cursor()

    # Get project base info
    cur.execute(
        """
        SELECT id, name, description, root_path, git_repo_path, current_branch,
               primary_language, framework, package_manager, metadata_json
        FROM projects
        WHERE id = ?
        """,
        (project_id,)
    )
    project_row = cur.fetchone()
    conn.close()

    if not project_row:
        return {"error": "Project not found"}

    root_path = project_row["root_path"]
    if not root_path or not os.path.exists(root_path):
        return {"error": "Project root path does not exist"}

    analysis = {
        "project_id": project_id,
        "name": project_row["name"],
        "description": project_row["description"],
        "root_path": root_path,
        "primary_language": project_row["primary_language"],
        "framework": project_row["framework"],
        "package_manager": project_row["package_manager"],
        "file_tree": {},
        "dependencies": {},
        "readme_content": None,
        "git_history": [],
        "entry_points": [],
        "project_structure": {},
        "ai_summary": ""
    }

    # 1. Build file tree
    try:
        analysis["file_tree"] = build_file_tree(root_path, max_depth=4)
    except Exception as e:
        analysis["file_tree"] = {"error": str(e)}

    # 2. Extract dependencies
    analysis["dependencies"] = extract_dependencies(root_path)

    # 3. Read README
    analysis["readme_content"] = read_readme(root_path)

    # 4. Get git history
    if project_row["git_repo_path"]:
        analysis["git_history"] = get_git_commit_history(
            project_row["git_repo_path"],
            limit=20
        )

    # 5. Detect entry points
    analysis["entry_points"] = detect_entry_points(root_path, project_row["primary_language"])

    # 6. Analyze project structure
    analysis["project_structure"] = analyze_project_structure(root_path)

    # 7. Generate AI summary
    analysis["ai_summary"] = generate_project_summary(analysis)

    return analysis


def extract_dependencies(root_path: str) -> Dict[str, Any]:
    """Extract dependencies from various package manifests."""
    dependencies = {
        "npm": None,
        "python": None,
        "rust": None,
        "go": None,
        "java": None,
        "dotnet": None
    }

    try:
        # Node.js / JavaScript
        package_json = Path(root_path) / "package.json"
        if package_json.exists():
            try:
                with open(package_json, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    dependencies["npm"] = {
                        "dependencies": data.get("dependencies", {}),
                        "devDependencies": data.get("devDependencies", {}),
                        "scripts": data.get("scripts", {}),
                        "main": data.get("main"),
                        "version": data.get("version")
                    }
            except Exception as e:
                dependencies["npm"] = {"error": str(e)}

        # Python
        requirements_txt = Path(root_path) / "requirements.txt"
        if requirements_txt.exists():
            try:
                with open(requirements_txt, "r", encoding="utf-8") as f:
                    lines = [line.strip() for line in f if line.strip() and not line.startswith("#")]
                    dependencies["python"] = {
                        "requirements": lines,
                        "count": len(lines)
                    }
            except Exception as e:
                dependencies["python"] = {"error": str(e)}

        # Python - pyproject.toml
        pyproject = Path(root_path) / "pyproject.toml"
        if pyproject.exists():
            try:
                with open(pyproject, "r", encoding="utf-8") as f:
                    content = f.read()
                    if not dependencies["python"]:
                        dependencies["python"] = {}
                    dependencies["python"]["pyproject_preview"] = content[:500]
            except Exception as e:
                pass

        # Rust
        cargo_toml = Path(root_path) / "Cargo.toml"
        if cargo_toml.exists():
            try:
                with open(cargo_toml, "r", encoding="utf-8") as f:
                    content = f.read()
                    dependencies["rust"] = {
                        "content_preview": content[:500]
                    }
            except Exception as e:
                dependencies["rust"] = {"error": str(e)}

        # Go
        go_mod = Path(root_path) / "go.mod"
        if go_mod.exists():
            try:
                with open(go_mod, "r", encoding="utf-8") as f:
                    content = f.read()
                    dependencies["go"] = {
                        "content_preview": content[:500]
                    }
            except Exception as e:
                dependencies["go"] = {"error": str(e)}

    except Exception as e:
        print(f"[Dependencies] Error: {e}")

    return dependencies


def read_readme(root_path: str) -> Optional[str]:
    """Read README file content."""
    readme_names = ["README.md", "README.MD", "README.txt", "README", "readme.md"]

    for name in readme_names:
        readme_path = Path(root_path) / name
        if readme_path.exists():
            try:
                with open(readme_path, "r", encoding="utf-8") as f:
                    content = f.read()
                    # Limit to first 5000 characters
                    return content[:5000] if len(content) > 5000 else content
            except Exception as e:
                return f"Error reading README: {e}"

    return None


def get_git_commit_history(repo_path: str, limit: int = 20) -> List[Dict[str, str]]:
    """Get recent git commit history."""
    commits = []

    try:
        result = subprocess.run(
            [
                "git", "log",
                f"-{limit}",
                "--pretty=format:%H|%an|%ae|%ad|%s",
                "--date=iso"
            ],
            cwd=repo_path,
            capture_output=True,
            text=True,
            timeout=10
        )

        if result.returncode == 0:
            for line in result.stdout.strip().split("\n"):
                if not line:
                    continue
                parts = line.split("|")
                if len(parts) >= 5:
                    commits.append({
                        "hash": parts[0][:8],
                        "author": parts[1],
                        "email": parts[2],
                        "date": parts[3],
                        "message": parts[4]
                    })

    except Exception as e:
        print(f"[Git History] Error: {e}")

    return commits


def detect_entry_points(root_path: str, language: Optional[str]) -> List[Dict[str, str]]:
    """Detect main entry points of the project."""
    entry_points = []

    try:
        root = Path(root_path)

        # Common entry point files
        entry_files = {
            "index.js": "JavaScript entry",
            "index.ts": "TypeScript entry",
            "main.js": "JavaScript main",
            "main.ts": "TypeScript main",
            "app.js": "JavaScript app",
            "app.ts": "TypeScript app",
            "server.js": "JavaScript server",
            "server.ts": "TypeScript server",
            "index.html": "HTML entry",
            "main.py": "Python main",
            "__main__.py": "Python package main",
            "app.py": "Python app",
            "manage.py": "Django management",
            "main.go": "Go main",
            "main.rs": "Rust main",
            "Program.cs": "C# program",
            "Main.java": "Java main"
        }

        # Check root level
        for filename, description in entry_files.items():
            file_path = root / filename
            if file_path.exists():
                entry_points.append({
                    "file": filename,
                    "path": str(file_path),
                    "description": description
                })

        # Check src/ directory
        src_dir = root / "src"
        if src_dir.exists():
            for filename, description in entry_files.items():
                file_path = src_dir / filename
                if file_path.exists():
                    entry_points.append({
                        "file": f"src/{filename}",
                        "path": str(file_path),
                        "description": description
                    })

        # Check package.json for scripts
        package_json = root / "package.json"
        if package_json.exists():
            try:
                with open(package_json, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    scripts = data.get("scripts", {})
                    if "start" in scripts:
                        entry_points.append({
                            "file": "package.json",
                            "path": "npm start",
                            "description": f"NPM start script: {scripts['start']}"
                        })
                    if "dev" in scripts:
                        entry_points.append({
                            "file": "package.json",
                            "path": "npm run dev",
                            "description": f"NPM dev script: {scripts['dev']}"
                        })
            except Exception:
                pass

    except Exception as e:
        print(f"[Entry Points] Error: {e}")

    return entry_points


def analyze_project_structure(root_path: str) -> Dict[str, Any]:
    """Analyze high-level project structure."""
    structure = {
        "directories": [],
        "file_types": {},
        "total_files": 0,
        "total_size_bytes": 0,
        "has_tests": False,
        "has_docs": False,
        "has_ci_cd": False,
        "has_docker": False
    }

    ignore_dirs = {
        "node_modules", ".git", "__pycache__", ".venv", "venv",
        "dist", "build", ".next", ".cache", "target", "bin", "obj"
    }

    try:
        root = Path(root_path)

        # Check for common directories
        common_dirs = ["src", "lib", "test", "tests", "docs", "doc", "scripts", "config"]
        for dir_name in common_dirs:
            dir_path = root / dir_name
            if dir_path.exists() and dir_path.is_dir():
                structure["directories"].append(dir_name)

        # Check for test directories
        if any(d in structure["directories"] for d in ["test", "tests"]):
            structure["has_tests"] = True

        # Check for docs
        if any(d in structure["directories"] for d in ["docs", "doc"]):
            structure["has_docs"] = True

        # Check for CI/CD
        ci_files = [".github", ".gitlab-ci.yml", ".travis.yml", "Jenkinsfile", ".circleci"]
        for ci_file in ci_files:
            if (root / ci_file).exists():
                structure["has_ci_cd"] = True
                break

        # Check for Docker
        if (root / "Dockerfile").exists() or (root / "docker-compose.yml").exists():
            structure["has_docker"] = True

        # Count files by extension
        for item in root.rglob("*"):
            if item.is_file():
                # Skip ignored directories
                if any(ignored in item.parts for ignored in ignore_dirs):
                    continue

                structure["total_files"] += 1
                try:
                    structure["total_size_bytes"] += item.stat().st_size
                except:
                    pass

                ext = item.suffix.lower()
                if ext:
                    structure["file_types"][ext] = structure["file_types"].get(ext, 0) + 1

    except Exception as e:
        print(f"[Project Structure] Error: {e}")

    return structure


def generate_project_summary(analysis: Dict[str, Any]) -> str:
    """Generate an AI-friendly summary of the project."""
    summary_parts = []

    # Basic info
    summary_parts.append(f"PROJECT: {analysis['name']}")
    if analysis['description']:
        summary_parts.append(f"Description: {analysis['description']}")

    # Language and framework
    if analysis['primary_language']:
        summary_parts.append(f"Primary Language: {analysis['primary_language']}")
    if analysis['framework']:
        summary_parts.append(f"Framework: {analysis['framework']}")

    # Structure
    structure = analysis.get('project_structure', {})
    if structure.get('total_files'):
        summary_parts.append(f"\nProject contains {structure['total_files']} files")

        # Most common file types
        file_types = structure.get('file_types', {})
        if file_types:
            top_types = sorted(file_types.items(), key=lambda x: x[1], reverse=True)[:5]
            summary_parts.append("Top file types: " + ", ".join([f"{ext} ({count})" for ext, count in top_types]))

    # Features
    features = []
    if structure.get('has_tests'):
        features.append("includes tests")
    if structure.get('has_docs'):
        features.append("has documentation")
    if structure.get('has_ci_cd'):
        features.append("CI/CD configured")
    if structure.get('has_docker'):
        features.append("Docker support")

    if features:
        summary_parts.append("Features: " + ", ".join(features))

    # Dependencies summary
    deps = analysis.get('dependencies', {})
    if deps.get('npm'):
        npm_deps = deps['npm']
        if isinstance(npm_deps, dict) and 'dependencies' in npm_deps:
            dep_count = len(npm_deps.get('dependencies', {}))
            dev_count = len(npm_deps.get('devDependencies', {}))
            summary_parts.append(f"\nNPM: {dep_count} dependencies, {dev_count} dev dependencies")

    if deps.get('python'):
        py_deps = deps['python']
        if isinstance(py_deps, dict) and 'count' in py_deps:
            summary_parts.append(f"Python: {py_deps['count']} requirements")

    # Entry points
    entry_points = analysis.get('entry_points', [])
    if entry_points:
        summary_parts.append(f"\nEntry Points: {', '.join([ep['file'] for ep in entry_points[:3]])}")

    # Git activity
    git_history = analysis.get('git_history', [])
    if git_history:
        recent = git_history[0]
        summary_parts.append(f"\nLast commit: {recent['message'][:60]} by {recent['author']}")

    # README preview
    readme = analysis.get('readme_content')
    if readme:
        # Get first paragraph
        lines = readme.split('\n')
        first_paragraph = []
        for line in lines:
            line = line.strip()
            if line and not line.startswith('#'):
                first_paragraph.append(line)
                if len(' '.join(first_paragraph)) > 200:
                    break

        if first_paragraph:
            summary_parts.append(f"\nREADME: {' '.join(first_paragraph)[:300]}")

    return "\n".join(summary_parts)
