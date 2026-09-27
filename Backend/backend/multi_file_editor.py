"""
Multi-File Editor System
========================

Intelligent multi-file editing with dependency tracking, import analysis,
and boilerplate generation for real app development.
"""

import os
import re
import ast
import pathlib
import json
import logging
from typing import Optional, Dict, Any, List, Set, Tuple
from dataclasses import dataclass, field
from enum import Enum

import requests

logger = logging.getLogger("sarah.multi_file_editor")


class FileOperation(str, Enum):
    """Types of file operations."""
    CREATE = "create"
    EDIT = "edit"
    DELETE = "delete"
    RENAME = "rename"


class FileType(str, Enum):
    """Common file types for boilerplate generation."""
    PYTHON_MODULE = "python_module"
    PYTHON_CLASS = "python_class"
    PYTHON_FUNCTION = "python_function"
    REACT_COMPONENT = "react_component"
    REACT_HOOK = "react_hook"
    API_ROUTE = "api_route"
    DATABASE_MODEL = "database_model"
    TEST_FILE = "test_file"
    CONFIG_FILE = "config_file"


@dataclass
class FileChange:
    """Represents a single file change operation."""
    operation: FileOperation
    file_path: str
    content: Optional[str] = None
    old_path: Optional[str] = None  # For rename operations
    reason: str = ""
    dependencies: List[str] = field(default_factory=list)
    imports_added: List[str] = field(default_factory=list)
    imports_removed: List[str] = field(default_factory=list)


@dataclass
class MultiFileEdit:
    """Represents a coordinated multi-file edit operation."""
    description: str
    changes: List[FileChange]
    execution_order: List[str]  # File paths in order they should be applied
    rollback_plan: List[Dict[str, Any]]
    risks: List[str] = field(default_factory=list)
    validation_steps: List[str] = field(default_factory=list)


# ---------------------------------------------------------
# DEPENDENCY ANALYSIS
# ---------------------------------------------------------

def analyze_python_imports(file_path: str) -> Dict[str, Any]:
    """
    Analyze Python file imports and dependencies.

    Returns:
        Dict with 'imports', 'from_imports', 'local_imports', 'external_imports'
    """
    if not os.path.exists(file_path):
        return {
            "imports": [],
            "from_imports": [],
            "local_imports": [],
            "external_imports": []
        }

    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            tree = ast.parse(f.read(), filename=file_path)
    except Exception as e:
        logger.warning(f"Failed to parse {file_path}: {e}")
        return {
            "imports": [],
            "from_imports": [],
            "local_imports": [],
            "external_imports": []
        }

    imports = []
    from_imports = []
    local_imports = []
    external_imports = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                module = alias.name
                imports.append(module)

                # Heuristic: local imports start with '.' or are relative
                if module.startswith('.'):
                    local_imports.append(module)
                else:
                    external_imports.append(module)

        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            names = [alias.name for alias in node.names]
            from_imports.append({"module": module, "names": names})

            if module.startswith('.') or node.level > 0:
                local_imports.append(module)
            else:
                external_imports.append(module)

    return {
        "imports": imports,
        "from_imports": from_imports,
        "local_imports": list(set(local_imports)),
        "external_imports": list(set(external_imports))
    }


def analyze_javascript_imports(file_path: str) -> Dict[str, Any]:
    """
    Analyze JavaScript/TypeScript file imports.

    Returns:
        Dict with 'imports', 'local_imports', 'external_imports'
    """
    if not os.path.exists(file_path):
        return {
            "imports": [],
            "local_imports": [],
            "external_imports": []
        }

    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
    except Exception as e:
        logger.warning(f"Failed to read {file_path}: {e}")
        return {
            "imports": [],
            "local_imports": [],
            "external_imports": []
        }

    # Regex patterns for different import styles
    # import foo from 'bar'
    # import { foo, bar } from 'baz'
    # const foo = require('bar')

    import_pattern = r"import\s+(?:(?:\{[^}]+\}|\w+)\s+from\s+)?['\"]([^'\"]+)['\"]"
    require_pattern = r"require\(['\"]([^'\"]+)['\"]\)"

    imports = []
    local_imports = []
    external_imports = []

    for match in re.finditer(import_pattern, content):
        module = match.group(1)
        imports.append(module)

        # Local imports start with './' or '../'
        if module.startswith('./') or module.startswith('../'):
            local_imports.append(module)
        else:
            external_imports.append(module)

    for match in re.finditer(require_pattern, content):
        module = match.group(1)
        if module not in imports:
            imports.append(module)

            if module.startswith('./') or module.startswith('../'):
                local_imports.append(module)
            else:
                external_imports.append(module)

    return {
        "imports": imports,
        "local_imports": list(set(local_imports)),
        "external_imports": list(set(external_imports))
    }


def build_dependency_graph(root_path: str, file_paths: List[str]) -> Dict[str, List[str]]:
    """
    Build a dependency graph for the given files.

    Args:
        root_path: Project root directory
        file_paths: List of file paths to analyze

    Returns:
        Dict mapping file paths to their dependencies
    """
    graph = {}

    for file_path in file_paths:
        full_path = os.path.join(root_path, file_path) if not os.path.isabs(file_path) else file_path

        if file_path.endswith('.py'):
            analysis = analyze_python_imports(full_path)
        elif file_path.endswith(('.js', '.jsx', '.ts', '.tsx')):
            analysis = analyze_javascript_imports(full_path)
        else:
            graph[file_path] = []
            continue

        # Map local imports to actual file paths
        dependencies = []
        for local_import in analysis.get('local_imports', []):
            # Resolve relative import to absolute path
            dep_path = resolve_import_path(full_path, local_import)
            if dep_path and dep_path in file_paths:
                dependencies.append(dep_path)

        graph[file_path] = dependencies

    return graph


def resolve_import_path(source_file: str, import_path: str) -> Optional[str]:
    """
    Resolve a relative import to an absolute file path.

    Args:
        source_file: The file containing the import
        import_path: The import path (e.g., './utils', '../config')

    Returns:
        Absolute file path or None if not found
    """
    source_dir = os.path.dirname(source_file)

    # Handle Python imports
    if not import_path.startswith('.'):
        return None

    # Remove leading dots and join with source directory
    relative_path = import_path.lstrip('.')

    # Try common extensions
    for ext in ['.py', '.js', '.jsx', '.ts', '.tsx', '/index.js', '/index.ts']:
        candidate = os.path.normpath(os.path.join(source_dir, relative_path + ext))
        if os.path.exists(candidate):
            return candidate

    return None


def topological_sort(graph: Dict[str, List[str]]) -> List[str]:
    """
    Topologically sort files based on dependencies.

    Files with no dependencies come first, files that depend on others come later.

    Args:
        graph: Dependency graph (file -> list of dependencies)

    Returns:
        List of files in execution order
    """
    # Calculate in-degree (number of dependencies)
    in_degree = {node: 0 for node in graph}
    for node in graph:
        for dep in graph[node]:
            if dep in in_degree:
                in_degree[dep] += 1

    # Start with nodes that have no dependencies
    queue = [node for node in in_degree if in_degree[node] == 0]
    result = []

    while queue:
        node = queue.pop(0)
        result.append(node)

        # Reduce in-degree for dependent nodes
        for dep_node in graph:
            if node in graph[dep_node]:
                in_degree[dep_node] -= 1
                if in_degree[dep_node] == 0:
                    queue.append(dep_node)

    # Check for cycles
    if len(result) != len(graph):
        logger.warning("Circular dependency detected in file graph")
        # Return remaining nodes anyway
        for node in graph:
            if node not in result:
                result.append(node)

    return result


# ---------------------------------------------------------
# BOILERPLATE GENERATION
# ---------------------------------------------------------

BOILERPLATE_TEMPLATES = {
    FileType.PYTHON_MODULE: '''"""
{description}
"""

import logging

logger = logging.getLogger(__name__)


# Your code here
''',

    FileType.PYTHON_CLASS: '''"""
{description}
"""

import logging

logger = logging.getLogger(__name__)


class {class_name}:
    """
    {class_description}
    """

    def __init__(self):
        """Initialize {class_name}."""
        pass

    def __repr__(self):
        return f"<{class_name}()>"
''',

    FileType.PYTHON_FUNCTION: '''"""
{description}
"""

from typing import Any


def {function_name}(*args: Any, **kwargs: Any) -> Any:
    """
    {function_description}

    Args:
        *args: Positional arguments
        **kwargs: Keyword arguments

    Returns:
        Result of the function
    """
    pass
''',

    FileType.REACT_COMPONENT: '''import React from 'react';
import PropTypes from 'prop-types';

/**
 * {description}
 */
const {component_name} = ({{ ...props }}) => {{
  return (
    <div className="{component_name_lower}">
      <h2>{component_name}</h2>
      {/* Your component content here */}
    </div>
  );
}};

{component_name}.propTypes = {{
  // Define your prop types here
}};

{component_name}.defaultProps = {{
  // Define default props here
}};

export default {component_name};
''',

    FileType.REACT_HOOK: '''import {{ useState, useEffect }} from 'react';

/**
 * {description}
 */
const {hook_name} = () => {{
  const [state, setState] = useState(null);

  useEffect(() => {{
    // Your effect logic here
    return () => {{
      // Cleanup logic here
    }};
  }}, []);

  return {{ state, setState }};
}};

export default {hook_name};
''',

    FileType.API_ROUTE: '''"""
{description}
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional

router = APIRouter()


class {model_name}Request(BaseModel):
    """Request model for {route_name}."""
    pass


class {model_name}Response(BaseModel):
    """Response model for {route_name}."""
    ok: bool
    message: Optional[str] = None


@router.get("{route_path}")
async def get_{route_name}():
    """
    GET {route_path}

    Returns:
        {model_name}Response
    """
    return {model_name}Response(ok=True, message="Success")


@router.post("{route_path}")
async def post_{route_name}(payload: {model_name}Request):
    """
    POST {route_path}

    Args:
        payload: Request payload

    Returns:
        {model_name}Response
    """
    return {model_name}Response(ok=True, message="Created")
''',

    FileType.DATABASE_MODEL: '''"""
{description}
"""

from sqlalchemy import Column, Integer, String, DateTime, Boolean
from sqlalchemy.ext.declarative import declarative_base
from datetime import datetime

Base = declarative_base()


class {model_name}(Base):
    """
    {model_description}
    """
    __tablename__ = '{table_name}'

    id = Column(Integer, primary_key=True, autoincrement=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    # Add your columns here

    def __repr__(self):
        return f"<{model_name}(id={{self.id}})>"

    def to_dict(self):
        """Convert model to dictionary."""
        return {{
            'id': self.id,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
        }}
''',

    FileType.TEST_FILE: '''"""
Tests for {module_name}
"""

import pytest
from {module_path} import {function_name}


class Test{class_name}:
    """Test suite for {class_name}."""

    def test_{function_name}_basic(self):
        """Test basic functionality of {function_name}."""
        # Arrange

        # Act
        result = {function_name}()

        # Assert
        assert result is not None

    def test_{function_name}_edge_cases(self):
        """Test edge cases for {function_name}."""
        # Add edge case tests here
        pass

    def test_{function_name}_error_handling(self):
        """Test error handling in {function_name}."""
        # Test error scenarios
        with pytest.raises(ValueError):
            {function_name}(invalid_input=True)
''',

    FileType.CONFIG_FILE: '''{{
  "name": "{config_name}",
  "description": "{description}",
  "version": "1.0.0",
  "settings": {{}}
}}
''',
}


def generate_boilerplate(
    file_type: FileType,
    **kwargs: Any
) -> str:
    """
    Generate boilerplate code for a given file type.

    Args:
        file_type: Type of file to generate
        **kwargs: Template variables

    Returns:
        Generated boilerplate code
    """
    template = BOILERPLATE_TEMPLATES.get(file_type)
    if not template:
        logger.warning("No boilerplate template registered for %s", file_type)
        return f"# Unsupported boilerplate file type: {file_type}\n"

    try:
        return template.format(**kwargs)
    except KeyError as e:
        logger.warning(f"Missing template variable for {file_type}: {e}")
        return template


# ---------------------------------------------------------
# AI-POWERED MULTI-FILE EDITING
# ---------------------------------------------------------

def _call_llm_multi_file_edit(
    user_request: str,
    context: Optional[Dict[str, Any]] = None
) -> Optional[Dict[str, Any]]:
    """
    Use the configured LLM (OpenRouter) to plan a multi-file edit operation.

    Args:
        user_request: User's request (e.g., "Add user authentication")
        context: Optional context about the project

    Returns:
        Structured multi-file edit plan
    """
    from backend.config import settings as _settings
    api_key = _settings.openrouter_api_key or os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        return None

    model = _settings.openrouter_model
    url = f"{_settings.openrouter_base_url}/chat/completions"

    system_prompt = """You are Sarah's Multi-File Editor, an expert at planning coordinated changes across multiple files.

Your job: Plan file operations (create, edit, delete, rename) to fulfill the user's request.

Respond with STRICT JSON ONLY (no markdown, no comments):
{
  "description": string,          // Overall description of the change
  "changes": [
    {
      "operation": string,         // "create", "edit", "delete", "rename"
      "file_path": string,         // Path to the file
      "content": string,           // Full file content (for create/edit)
      "old_path": string,          // Old path (for rename only)
      "reason": string,            // Why this change is needed
      "dependencies": string[],    // Files this depends on
      "imports_added": string[],   // New imports added
      "imports_removed": string[]  // Imports removed
    }
  ],
  "execution_order": string[],    // File paths in order they should be modified
  "risks": string[],              // Potential risks or issues
  "validation_steps": string[]    // How to verify the changes work
}

Key principles:
1. Order matters - dependencies must be created/edited before dependents
2. Be specific about imports and dependencies
3. Generate complete, working code (not TODO comments)
4. Consider edge cases and error handling
5. Follow the project's existing patterns"""

    user_prompt = f"""Plan a multi-file edit for this request:

REQUEST: {user_request}"""

    if context:
        user_prompt += f"\n\nPROJECT CONTEXT:\n{json.dumps(context, indent=2)}"

    user_prompt += """

Create a detailed plan that:
1. Identifies all files that need to be created/modified/deleted
2. Specifies the complete content for each file
3. Lists dependencies between files
4. Provides an execution order
5. Identifies risks and validation steps"""

    payload = {
        "model": model,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "max_tokens": 8192,
        "temperature": 0.3,
    }

    try:
        resp = requests.post(
            url,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            timeout=90,
            json=payload,
        )
        resp.raise_for_status()
        data = resp.json()

        choices = data.get("choices") or []
        if not choices:
            return None

        content = choices[0].get("message", {}).get("content", "")
        if isinstance(content, str):
            return json.loads(content.strip())

        return None
    except Exception as exc:
        logger.warning(f"LLM multi-file edit failed: {exc}")
        return None


def plan_multi_file_edit(
    user_request: str,
    project_root: Optional[str] = None,
    existing_files: Optional[List[str]] = None,
    use_ai: bool = True
) -> MultiFileEdit:
    """
    Plan a coordinated multi-file edit operation.

    Args:
        user_request: Description of what to implement
        project_root: Root directory of the project
        existing_files: List of existing files in the project
        use_ai: Whether to use AI for planning

    Returns:
        MultiFileEdit plan
    """
    context = {}
    if project_root:
        context["project_root"] = project_root
    if existing_files:
        context["existing_files"] = existing_files[:50]  # Limit to first 50

    if use_ai:
        result = _call_llm_multi_file_edit(user_request, context)
        if result:
            # Convert to FileChange objects
            changes = []
            for change_data in result.get("changes", []):
                change = FileChange(
                    operation=FileOperation(change_data.get("operation", "create")),
                    file_path=change_data.get("file_path", ""),
                    content=change_data.get("content"),
                    old_path=change_data.get("old_path"),
                    reason=change_data.get("reason", ""),
                    dependencies=change_data.get("dependencies", []),
                    imports_added=change_data.get("imports_added", []),
                    imports_removed=change_data.get("imports_removed", []),
                )
                changes.append(change)

            # Build rollback plan
            rollback_plan = []
            for change in changes:
                if change.operation == FileOperation.CREATE:
                    rollback_plan.append({"action": "delete", "file": change.file_path})
                elif change.operation == FileOperation.DELETE:
                    rollback_plan.append({"action": "restore", "file": change.file_path})
                elif change.operation == FileOperation.EDIT:
                    rollback_plan.append({"action": "restore", "file": change.file_path})

            return MultiFileEdit(
                description=result.get("description", user_request),
                changes=changes,
                execution_order=result.get("execution_order", []),
                rollback_plan=rollback_plan,
                risks=result.get("risks", []),
                validation_steps=result.get("validation_steps", [])
            )

    # Fallback: Simple single-file edit
    return MultiFileEdit(
        description=user_request,
        changes=[],
        execution_order=[],
        rollback_plan=[],
        risks=["AI-powered multi-file editing unavailable"],
        validation_steps=["Manual verification required"]
    )


def format_multi_file_edit(edit: MultiFileEdit, include_content: bool = False) -> str:
    """
    Format multi-file edit plan into human-readable text.

    Args:
        edit: MultiFileEdit plan
        include_content: Whether to include full file contents

    Returns:
        Formatted string
    """
    lines = ["📝 MULTI-FILE EDIT PLAN\n"]

    lines.append(f"Description: {edit.description}\n")

    lines.append(f"Changes ({len(edit.changes)}):")
    for i, change in enumerate(edit.changes, 1):
        op_emoji = {
            FileOperation.CREATE: "✨",
            FileOperation.EDIT: "✏️",
            FileOperation.DELETE: "🗑️",
            FileOperation.RENAME: "📝"
        }.get(change.operation, "📄")

        lines.append(f"\n{op_emoji} [{i}] {change.operation.upper()}: {change.file_path}")
        if change.reason:
            lines.append(f"   Reason: {change.reason}")

        if change.dependencies:
            lines.append(f"   Depends on: {', '.join(change.dependencies)}")

        if change.imports_added:
            lines.append(f"   Imports added: {', '.join(change.imports_added)}")

        if include_content and change.content:
            lines.append(f"   Content preview:")
            preview = change.content[:200].replace('\n', '\n   ')
            lines.append(f"   {preview}...")

    if edit.execution_order:
        lines.append(f"\n⚡ Execution Order:")
        for i, file_path in enumerate(edit.execution_order, 1):
            lines.append(f"   {i}. {file_path}")

    if edit.risks:
        lines.append(f"\n⚠️ Risks:")
        for risk in edit.risks:
            lines.append(f"   • {risk}")

    if edit.validation_steps:
        lines.append(f"\n✅ Validation Steps:")
        for step in edit.validation_steps:
            lines.append(f"   • {step}")

    return "\n".join(lines)
