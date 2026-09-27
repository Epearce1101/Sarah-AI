"""
Code Delivery System
===================

Improved system for delivering code changes from AI to users with:
- Visual diffs
- One-click apply
- Validation
- Rollback support
- Multiple delivery formats
"""

import os
import re
import difflib
import hashlib
import json
import pathlib
import shutil
import subprocess
from typing import Optional, Dict, Any, List, Tuple
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
import logging

import requests

logger = logging.getLogger("sarah.code_delivery")


class DeliveryFormat(str, Enum):
    """Code delivery formats."""
    INLINE = "inline"              # Direct code block in chat
    DIFF = "diff"                  # Unified diff format
    SPLIT_VIEW = "split_view"      # Side-by-side comparison
    FILE_PATCH = "file_patch"      # Git-style patch file
    CLIPBOARD = "clipboard"        # Copy to clipboard
    DIRECT_APPLY = "direct_apply"  # Apply directly to file


class ChangeType(str, Enum):
    """Types of code changes."""
    CREATE = "create"
    EDIT = "edit"
    DELETE = "delete"
    RENAME = "rename"


@dataclass
class CodeChange:
    """Represents a single code change."""
    change_id: str
    file_path: str
    change_type: ChangeType
    old_content: Optional[str] = None
    new_content: Optional[str] = None
    old_path: Optional[str] = None  # For renames
    description: str = ""
    language: str = "python"
    line_start: Optional[int] = None
    line_end: Optional[int] = None
    validation_errors: List[str] = field(default_factory=list)
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())


@dataclass
class CodeDelivery:
    """Complete code delivery package."""
    delivery_id: str
    changes: List[CodeChange]
    summary: str
    format: DeliveryFormat
    total_lines_added: int = 0
    total_lines_removed: int = 0
    files_affected: int = 0
    validation_passed: bool = True
    can_auto_apply: bool = True
    warnings: List[str] = field(default_factory=list)
    backup_created: bool = False
    backup_path: Optional[str] = None


# ---------------------------------------------------------
# DIFF GENERATION
# ---------------------------------------------------------

def generate_unified_diff(
    old_content: str,
    new_content: str,
    file_path: str,
    context_lines: int = 3
) -> str:
    """
    Generate a unified diff between old and new content.

    Args:
        old_content: Original file content
        new_content: New file content
        file_path: Path to the file (for display)
        context_lines: Number of context lines to show

    Returns:
        Unified diff string
    """
    old_lines = old_content.splitlines(keepends=True)
    new_lines = new_content.splitlines(keepends=True)

    diff = difflib.unified_diff(
        old_lines,
        new_lines,
        fromfile=f"a/{file_path}",
        tofile=f"b/{file_path}",
        lineterm='',
        n=context_lines
    )

    return ''.join(diff)


def generate_side_by_side_diff(
    old_content: str,
    new_content: str,
    file_path: str,
    width: int = 80
) -> str:
    """
    Generate a side-by-side diff view.

    Args:
        old_content: Original content
        new_content: New content
        file_path: File path for display
        width: Column width for each side

    Returns:
        Side-by-side diff string
    """
    old_lines = old_content.splitlines()
    new_lines = new_content.splitlines()

    diff = difflib.HtmlDiff(wrapcolumn=width)
    html_diff = diff.make_table(
        old_lines,
        new_lines,
        fromdesc=f"Original: {file_path}",
        todesc=f"Modified: {file_path}",
        context=True,
        numlines=3
    )

    # Convert HTML to text for terminal display
    # (In practice, this would be rendered in the frontend)
    return html_diff


def generate_change_stats(old_content: str, new_content: str) -> Dict[str, int]:
    """
    Calculate change statistics.

    Returns:
        Dict with additions, deletions, changes
    """
    old_lines = old_content.splitlines()
    new_lines = new_content.splitlines()

    diff = difflib.unified_diff(old_lines, new_lines, lineterm='')

    additions = 0
    deletions = 0

    for line in diff:
        if line.startswith('+') and not line.startswith('+++'):
            additions += 1
        elif line.startswith('-') and not line.startswith('---'):
            deletions += 1

    return {
        "additions": additions,
        "deletions": deletions,
        "changes": additions + deletions,
        "net_change": additions - deletions
    }


# ---------------------------------------------------------
# CODE VALIDATION
# ---------------------------------------------------------

def validate_python_syntax(code: str) -> Tuple[bool, List[str]]:
    """
    Validate Python code syntax.

    Returns:
        (is_valid, list of errors)
    """
    import ast
    errors = []

    try:
        ast.parse(code)
        return True, []
    except SyntaxError as e:
        errors.append(f"SyntaxError at line {e.lineno}: {e.msg}")
        return False, errors
    except Exception as e:
        errors.append(f"Parse error: {str(e)}")
        return False, errors


def validate_javascript_syntax(code: str) -> Tuple[bool, List[str]]:
    """
    Validate JavaScript syntax (requires Node.js).

    Returns:
        (is_valid, list of errors)
    """
    errors = []

    # Try to use Node.js to validate syntax
    try:
        result = subprocess.run(
            ["node", "--check", "-"],
            input=code,
            capture_output=True,
            text=True,
            timeout=5
        )

        if result.returncode == 0:
            return True, []
        else:
            errors.append(result.stderr.strip())
            return False, errors

    except FileNotFoundError:
        # Node.js not available, skip validation
        return True, ["Syntax validation skipped (Node.js not installed)"]
    except subprocess.TimeoutExpired:
        errors.append("Syntax validation timed out")
        return False, errors
    except Exception as e:
        errors.append(f"Validation error: {str(e)}")
        return False, errors


def validate_code_change(change: CodeChange) -> CodeChange:
    """
    Validate a code change and update validation errors.

    Args:
        change: CodeChange to validate

    Returns:
        Updated CodeChange with validation results
    """
    if change.change_type == ChangeType.DELETE:
        # No validation needed for deletions
        return change

    if not change.new_content:
        change.validation_errors.append("No content provided")
        return change

    # Validate based on language
    is_valid = True
    errors = []

    if change.language in ["python", "py"]:
        is_valid, errors = validate_python_syntax(change.new_content)
    elif change.language in ["javascript", "js", "typescript", "ts", "jsx", "tsx"]:
        is_valid, errors = validate_javascript_syntax(change.new_content)

    change.validation_errors.extend(errors)

    return change


# ---------------------------------------------------------
# FILE OPERATIONS WITH BACKUP
# ---------------------------------------------------------

def create_backup(file_path: str) -> Optional[str]:
    """
    Create a backup of a file before modification.

    Args:
        file_path: Path to file to backup

    Returns:
        Path to backup file or None if failed
    """
    if not os.path.exists(file_path):
        return None

    # Create backup directory
    backup_dir = pathlib.Path.home() / ".sarah" / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)

    # Generate backup filename with timestamp
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    file_name = os.path.basename(file_path)
    backup_name = f"{file_name}.{timestamp}.bak"
    backup_path = backup_dir / backup_name

    try:
        shutil.copy2(file_path, backup_path)
        logger.info(f"Created backup: {backup_path}")
        return str(backup_path)
    except Exception as e:
        logger.error(f"Failed to create backup: {e}")
        return None


def apply_change_to_file(change: CodeChange, create_backup_first: bool = True) -> bool:
    """
    Apply a code change to the actual file.

    Args:
        change: CodeChange to apply
        create_backup_first: Whether to create backup before applying

    Returns:
        True if successful
    """
    file_path = pathlib.Path(change.file_path)

    try:
        # Create backup if requested
        if create_backup_first and file_path.exists():
            backup_path = create_backup(str(file_path))
            if not backup_path:
                logger.warning(f"Failed to create backup for {file_path}")

        # Apply change based on type
        if change.change_type == ChangeType.CREATE:
            # Create parent directories if needed
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.write_text(change.new_content, encoding='utf-8')

        elif change.change_type == ChangeType.EDIT:
            file_path.write_text(change.new_content, encoding='utf-8')

        elif change.change_type == ChangeType.DELETE:
            file_path.unlink()

        elif change.change_type == ChangeType.RENAME:
            if change.old_path:
                old_path = pathlib.Path(change.old_path)
                old_path.rename(file_path)

        logger.info(f"Applied change to {file_path}")
        return True

    except Exception as e:
        logger.error(f"Failed to apply change to {file_path}: {e}")
        return False


# ---------------------------------------------------------
# AI-POWERED CODE FORMATTING
# ---------------------------------------------------------

def _call_llm_format_code(
    code: str,
    language: str,
    format_style: str = "readable"
) -> Optional[str]:
    """
    Use the configured LLM (OpenRouter) to format code for better readability.

    Args:
        code: Code to format
        language: Programming language
        format_style: "readable", "compact", "standard"

    Returns:
        Formatted code or None
    """
    from backend import llm_models
    from backend.config import settings as _settings
    api_key = _settings.openrouter_api_key or os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        return None

    model = llm_models.current_online_model()
    url = f"{_settings.openrouter_base_url}/chat/completions"

    style_instructions = {
        "readable": "Format for maximum readability with clear spacing and comments",
        "compact": "Format for minimal lines while maintaining clarity",
        "standard": "Format following standard style guides (PEP 8, Standard JS, etc.)"
    }

    system_prompt = f"""You are a code formatter. Format the provided {language} code following these rules:

1. {style_instructions.get(format_style, style_instructions['standard'])}
2. Preserve all functionality
3. Fix indentation
4. Add/improve comments for complex logic
5. Follow best practices

IMPORTANT: Return ONLY the formatted code, no explanations or markdown."""

    payload = {
        "model": model,
        "models": llm_models.request_models(),
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"Format this {language} code:\n\n{code}"},
        ],
        "max_tokens": 4096,
        "temperature": 0.1,
    }

    try:
        resp = requests.post(
            url,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            timeout=30,
            json=payload,
        )
        resp.raise_for_status()
        data = resp.json()

        choices = data.get("choices") or []
        if not choices:
            return None

        formatted = choices[0].get("message", {}).get("content", "")

        # Remove markdown code blocks if present
        formatted = re.sub(r'^```[\w]*\n', '', formatted)
        formatted = re.sub(r'\n```$', '', formatted)

        return formatted.strip()

    except Exception as exc:
        logger.warning(f"Code formatting failed: {exc}")
        return None


# ---------------------------------------------------------
# CODE DELIVERY PACKAGE CREATION
# ---------------------------------------------------------

def create_code_delivery(
    changes: List[CodeChange],
    summary: str,
    format: DeliveryFormat = DeliveryFormat.DIFF,
    validate: bool = True,
    auto_format: bool = False
) -> CodeDelivery:
    """
    Create a complete code delivery package.

    Args:
        changes: List of code changes
        summary: Summary of what was changed
        format: Delivery format
        validate: Whether to validate syntax
        auto_format: Whether to auto-format code

    Returns:
        CodeDelivery package
    """
    delivery_id = hashlib.md5(
        (summary + str(datetime.now())).encode()
    ).hexdigest()[:12]

    # Validate changes if requested
    if validate:
        changes = [validate_code_change(c) for c in changes]

    # Auto-format code if requested
    if auto_format:
        for change in changes:
            if change.new_content and change.change_type in [ChangeType.CREATE, ChangeType.EDIT]:
                formatted = _call_llm_format_code(
                    change.new_content,
                    change.language
                )
                if formatted:
                    change.new_content = formatted

    # Calculate statistics
    total_additions = 0
    total_deletions = 0
    files_affected = len(changes)
    validation_passed = all(len(c.validation_errors) == 0 for c in changes)

    for change in changes:
        if change.old_content and change.new_content:
            stats = generate_change_stats(change.old_content, change.new_content)
            total_additions += stats["additions"]
            total_deletions += stats["deletions"]

    # Determine if can auto-apply
    can_auto_apply = validation_passed and all(
        c.change_type != ChangeType.DELETE for c in changes
    )

    # Collect warnings
    warnings = []
    if not validation_passed:
        warnings.append("Some changes have validation errors")

    for change in changes:
        if change.change_type == ChangeType.DELETE:
            warnings.append(f"Will delete file: {change.file_path}")

    return CodeDelivery(
        delivery_id=delivery_id,
        changes=changes,
        summary=summary,
        format=format,
        total_lines_added=total_additions,
        total_lines_removed=total_deletions,
        files_affected=files_affected,
        validation_passed=validation_passed,
        can_auto_apply=can_auto_apply,
        warnings=warnings
    )


def format_code_delivery(
    delivery: CodeDelivery,
    include_diffs: bool = True,
    include_full_content: bool = False
) -> str:
    """
    Format code delivery into human-readable text.

    Args:
        delivery: CodeDelivery to format
        include_diffs: Whether to include diff output
        include_full_content: Whether to include full file contents

    Returns:
        Formatted string
    """
    lines = ["📦 CODE DELIVERY PACKAGE\n"]

    lines.append(f"ID: {delivery.delivery_id}")
    lines.append(f"Summary: {delivery.summary}\n")

    lines.append(f"📊 Statistics:")
    lines.append(f"  Files affected: {delivery.files_affected}")
    lines.append(f"  Lines added: +{delivery.total_lines_added}")
    lines.append(f"  Lines removed: -{delivery.total_lines_removed}")
    lines.append(f"  Net change: {delivery.total_lines_added - delivery.total_lines_removed:+d}\n")

    if delivery.warnings:
        lines.append("⚠️ Warnings:")
        for warning in delivery.warnings:
            lines.append(f"  • {warning}")
        lines.append("")

    lines.append(f"✅ Validation: {'PASSED' if delivery.validation_passed else 'FAILED'}")
    lines.append(f"🚀 Can auto-apply: {'YES' if delivery.can_auto_apply else 'NO'}\n")

    lines.append(f"📝 Changes ({len(delivery.changes)}):")

    for i, change in enumerate(delivery.changes, 1):
        change_emoji = {
            ChangeType.CREATE: "✨",
            ChangeType.EDIT: "✏️",
            ChangeType.DELETE: "🗑️",
            ChangeType.RENAME: "📝"
        }.get(change.change_type, "📄")

        lines.append(f"\n{change_emoji} [{i}] {change.change_type.upper()}: {change.file_path}")

        if change.description:
            lines.append(f"   {change.description}")

        if change.validation_errors:
            lines.append(f"   ❌ Validation errors:")
            for error in change.validation_errors:
                lines.append(f"      • {error}")

        if include_diffs and change.old_content and change.new_content:
            diff = generate_unified_diff(
                change.old_content,
                change.new_content,
                change.file_path
            )
            if diff:
                lines.append(f"\n   Diff:")
                for line in diff.splitlines()[:50]:  # Limit diff lines
                    lines.append(f"   {line}")
                if len(diff.splitlines()) > 50:
                    lines.append(f"   ... ({len(diff.splitlines()) - 50} more lines)")

        if include_full_content and change.new_content:
            lines.append(f"\n   Full content:")
            lines.append(f"   ```{change.language}")
            for line in change.new_content.splitlines()[:30]:
                lines.append(f"   {line}")
            if len(change.new_content.splitlines()) > 30:
                lines.append(f"   ... ({len(change.new_content.splitlines()) - 30} more lines)")
            lines.append(f"   ```")

    lines.append("\n💡 Next Steps:")
    if delivery.can_auto_apply:
        lines.append("  1. Review the changes above")
        lines.append("  2. Click 'Apply Changes' to automatically apply")
        lines.append("  3. Or manually copy/paste each change")
    else:
        lines.append("  1. Fix validation errors first")
        lines.append("  2. Review changes carefully")
        lines.append("  3. Apply manually after verification")

    return "\n".join(lines)


def apply_delivery(
    delivery: CodeDelivery,
    create_backups: bool = True,
    dry_run: bool = False
) -> Dict[str, Any]:
    """
    Apply all changes in a delivery package.

    Args:
        delivery: CodeDelivery to apply
        create_backups: Whether to create backups
        dry_run: If True, don't actually apply changes

    Returns:
        Dict with results
    """
    results = {
        "success": False,
        "applied": [],
        "failed": [],
        "skipped": [],
        "backups": []
    }

    if not delivery.can_auto_apply and not dry_run:
        results["skipped"] = [c.file_path for c in delivery.changes]
        return results

    for change in delivery.changes:
        if dry_run:
            results["applied"].append(change.file_path)
            continue

        success = apply_change_to_file(change, create_backup_first=create_backups)

        if success:
            results["applied"].append(change.file_path)
        else:
            results["failed"].append(change.file_path)

    results["success"] = len(results["failed"]) == 0

    return results
