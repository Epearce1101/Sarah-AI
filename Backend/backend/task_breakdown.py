"""
Task Breakdown System
====================

Intelligent task decomposition system that breaks down complex tasks into
manageable subtasks with dependencies, priorities, and tracking.
"""

import os
import base64
import json
import logging
from typing import Optional, Dict, Any, List
from datetime import datetime
from enum import Enum

import requests

from backend.config import settings as _settings

logger = logging.getLogger("sarah.task_breakdown")


class TaskPriority(str, Enum):
    """Task priority levels."""
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class TaskStatus(str, Enum):
    """Task completion status."""
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    BLOCKED = "blocked"
    CANCELLED = "cancelled"


class Task:
    """Represents a single task in the breakdown."""

    def __init__(
        self,
        task_id: str,
        title: str,
        description: str,
        priority: TaskPriority = TaskPriority.MEDIUM,
        estimated_complexity: int = 1,  # 1-5 scale
        dependencies: Optional[List[str]] = None,
        tags: Optional[List[str]] = None,
    ):
        self.task_id = task_id
        self.title = title
        self.description = description
        self.priority = priority
        self.estimated_complexity = estimated_complexity
        self.dependencies = dependencies or []
        self.tags = tags or []
        self.status = TaskStatus.PENDING
        self.created_at = datetime.now().isoformat()
        self.completed_at: Optional[str] = None
        self.notes: List[str] = []

    def to_dict(self) -> Dict[str, Any]:
        """Convert task to dictionary."""
        return {
            "task_id": self.task_id,
            "title": self.title,
            "description": self.description,
            "priority": self.priority,
            "estimated_complexity": self.estimated_complexity,
            "dependencies": self.dependencies,
            "tags": self.tags,
            "status": self.status,
            "created_at": self.created_at,
            "completed_at": self.completed_at,
            "notes": self.notes,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Task":
        """Create task from dictionary."""
        task = cls(
            task_id=data["task_id"],
            title=data["title"],
            description=data["description"],
            priority=data.get("priority", TaskPriority.MEDIUM),
            estimated_complexity=data.get("estimated_complexity", 1),
            dependencies=data.get("dependencies", []),
            tags=data.get("tags", []),
        )
        task.status = data.get("status", TaskStatus.PENDING)
        task.created_at = data.get("created_at", datetime.now().isoformat())
        task.completed_at = data.get("completed_at")
        task.notes = data.get("notes", [])
        return task


# ---------------------------------------------------------
# AI-POWERED TASK BREAKDOWN
# ---------------------------------------------------------

def _call_llm_task_breakdown(user_request: str, context: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """
    Use the configured LLM (OpenRouter) to break down a user request into subtasks.

    Args:
        user_request: The user's high-level request or goal
        context: Optional additional context about the project

    Returns:
        Structured task breakdown with dependencies and priorities
    """
    api_key = _settings.openrouter_api_key or os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        return None

    model = _settings.openrouter_model
    url = f"{_settings.openrouter_base_url}/chat/completions"

    system_prompt = """You are Sarah's Task Planning Assistant, an expert at breaking down complex development tasks.

Your job: Decompose user requests into actionable, ordered subtasks.

Respond with STRICT JSON ONLY (no markdown, no comments):
{
  "main_goal": string,           // Restate the main goal clearly
  "estimated_total_time": string, // e.g., "2-3 hours", "1 day", "1 week"
  "complexity": number,          // 1-10 overall complexity score
  "tasks": [
    {
      "task_id": string,         // Unique ID like "task_1", "task_2"
      "title": string,           // Short title (3-7 words)
      "description": string,     // Detailed description of what needs to be done
      "priority": string,        // "critical", "high", "medium", "low"
      "estimated_complexity": number,  // 1-5 complexity for this task
      "dependencies": string[],  // Array of task_ids this depends on
      "tags": string[],          // Relevant tags: "frontend", "backend", "database", "testing", etc.
      "files_to_modify": string[],  // Specific files that will likely need changes
      "acceptance_criteria": string[]  // How to verify this task is complete
    }
  ],
  "critical_path": string[],     // Task IDs in order for fastest completion
  "risks": string[],             // Potential risks or blockers
  "suggestions": string[]        // Additional suggestions or considerations
}

Key principles:
1. Break complex tasks into 3-10 subtasks (not too granular, not too vague)
2. Identify true dependencies (what MUST happen before what)
3. Tag tasks appropriately for filtering
4. Be specific about files and acceptance criteria
5. Consider the critical path for efficient execution"""

    user_prompt = f"""Break down this development task into actionable subtasks:

REQUEST: {user_request}"""

    if context:
        user_prompt += f"\n\nADDITIONAL CONTEXT:\n{context}"

    user_prompt += """

Provide a detailed task breakdown that:
1. Identifies all necessary subtasks
2. Establishes dependencies (which tasks must happen first)
3. Assigns appropriate priorities
4. Specifies which files will need modification
5. Defines clear acceptance criteria for each task"""

    payload = {
        "model": model,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "max_tokens": 4096,
        "temperature": 0.3,  # Some creativity but mostly structured
    }

    try:
        resp = requests.post(
            url,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            timeout=60,
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
        logger.warning("LLM task breakdown failed: %s", exc)
        return None


def breakdown_task(
    user_request: str,
    context: Optional[str] = None,
    use_ai: bool = True
) -> Dict[str, Any]:
    """
    Break down a user request into subtasks.

    Args:
        user_request: The user's high-level request
        context: Optional project context
        use_ai: Whether to use AI for breakdown (fallback to simple breakdown if False)

    Returns:
        Task breakdown structure
    """
    if use_ai:
        result = _call_llm_task_breakdown(user_request, context)
        if result:
            # Convert to Task objects
            tasks = []
            for task_data in result.get("tasks", []):
                task = Task(
                    task_id=task_data.get("task_id", f"task_{len(tasks) + 1}"),
                    title=task_data.get("title", "Untitled Task"),
                    description=task_data.get("description", ""),
                    priority=TaskPriority(task_data.get("priority", "medium")),
                    estimated_complexity=task_data.get("estimated_complexity", 3),
                    dependencies=task_data.get("dependencies", []),
                    tags=task_data.get("tags", []),
                )
                # Add additional metadata
                task.files_to_modify = task_data.get("files_to_modify", [])
                task.acceptance_criteria = task_data.get("acceptance_criteria", [])
                tasks.append(task)

            return {
                "main_goal": result.get("main_goal", user_request),
                "estimated_total_time": result.get("estimated_total_time", "unknown"),
                "complexity": result.get("complexity", 5),
                "tasks": [t.to_dict() for t in tasks],
                "critical_path": result.get("critical_path", []),
                "risks": result.get("risks", []),
                "suggestions": result.get("suggestions", []),
                "breakdown_method": "ai"
            }

    # Fallback: Simple heuristic-based breakdown
    return _simple_task_breakdown(user_request)


def _simple_task_breakdown(user_request: str) -> Dict[str, Any]:
    """
    Simple heuristic-based task breakdown when AI is not available.
    """
    # Basic heuristic: split on common task indicators
    keywords = {
        "implement": ["Design implementation", "Write code", "Test functionality"],
        "add": ["Design feature", "Implement feature", "Test feature"],
        "fix": ["Reproduce bug", "Identify root cause", "Implement fix", "Verify fix"],
        "refactor": ["Analyze current code", "Plan refactoring", "Refactor code", "Test changes"],
        "optimize": ["Profile performance", "Identify bottlenecks", "Implement optimizations", "Verify improvements"],
    }

    # Try to match keywords
    request_lower = user_request.lower()
    tasks = []

    for keyword, subtasks in keywords.items():
        if keyword in request_lower:
            for i, subtask in enumerate(subtasks):
                task = Task(
                    task_id=f"task_{i + 1}",
                    title=subtask,
                    description=f"{subtask} for: {user_request}",
                    priority=TaskPriority.MEDIUM,
                    estimated_complexity=3,
                    dependencies=[f"task_{i}"] if i > 0 else [],
                    tags=["auto-generated"],
                )
                tasks.append(task)
            break

    # Default fallback
    if not tasks:
        tasks = [
            Task(
                task_id="task_1",
                title="Analyze Requirements",
                description=f"Understand requirements for: {user_request}",
                priority=TaskPriority.HIGH,
                estimated_complexity=2,
            ),
            Task(
                task_id="task_2",
                title="Implement Solution",
                description=f"Implement: {user_request}",
                priority=TaskPriority.HIGH,
                estimated_complexity=4,
                dependencies=["task_1"],
            ),
            Task(
                task_id="task_3",
                title="Test and Verify",
                description=f"Test the implementation of: {user_request}",
                priority=TaskPriority.MEDIUM,
                estimated_complexity=2,
                dependencies=["task_2"],
            ),
        ]

    return {
        "main_goal": user_request,
        "estimated_total_time": "unknown",
        "complexity": 5,
        "tasks": [t.to_dict() for t in tasks],
        "critical_path": [t.task_id for t in tasks],
        "risks": ["AI-powered breakdown unavailable, using heuristic fallback"],
        "suggestions": ["Enable AI-powered breakdown by setting SARAH_OPENROUTER_API_KEY"],
        "breakdown_method": "heuristic"
    }


# ---------------------------------------------------------
# TASK FORMATTING AND VISUALIZATION
# ---------------------------------------------------------

def format_task_breakdown(breakdown: Dict[str, Any], include_details: bool = True) -> str:
    """
    Format task breakdown into human-readable text.

    Args:
        breakdown: Task breakdown structure
        include_details: Whether to include detailed information

    Returns:
        Formatted text representation
    """
    lines = ["📋 TASK BREAKDOWN\n"]

    lines.append(f"Goal: {breakdown.get('main_goal', 'Unknown')}")
    lines.append(f"Estimated Time: {breakdown.get('estimated_total_time', 'Unknown')}")
    lines.append(f"Complexity: {breakdown.get('complexity', 0)}/10\n")

    tasks = breakdown.get("tasks", [])
    lines.append(f"Tasks ({len(tasks)}):")

    for task in tasks:
        priority = task.get("priority", "medium")
        status = task.get("status", "pending")

        priority_emoji = {
            "critical": "🔴",
            "high": "🟡",
            "medium": "🔵",
            "low": "🟢"
        }.get(priority, "⚪")

        status_emoji = {
            "pending": "⏳",
            "in_progress": "🔄",
            "completed": "✅",
            "blocked": "🚫",
            "cancelled": "❌"
        }.get(status, "❓")

        title = task.get("title", "Untitled")
        task_id = task.get("task_id", "")

        lines.append(f"\n{status_emoji} {priority_emoji} [{task_id}] {title}")

        if include_details:
            desc = task.get("description", "")
            if desc:
                lines.append(f"   {desc}")

            deps = task.get("dependencies", [])
            if deps:
                lines.append(f"   Depends on: {', '.join(deps)}")

            tags = task.get("tags", [])
            if tags:
                lines.append(f"   Tags: {', '.join(tags)}")

            complexity = task.get("estimated_complexity", 0)
            lines.append(f"   Complexity: {complexity}/5")

    critical_path = breakdown.get("critical_path", [])
    if critical_path:
        lines.append(f"\n⚡ Critical Path: {' → '.join(critical_path)}")

    risks = breakdown.get("risks", [])
    if risks:
        lines.append("\n⚠️ Risks:")
        for risk in risks:
            lines.append(f"  • {risk}")

    suggestions = breakdown.get("suggestions", [])
    if suggestions:
        lines.append("\n💡 Suggestions:")
        for suggestion in suggestions:
            lines.append(f"  • {suggestion}")

    return "\n".join(lines)


def get_next_task(breakdown: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Get the next task that should be worked on based on dependencies and priorities.

    Args:
        breakdown: Task breakdown structure

    Returns:
        Next task to work on, or None if all tasks are completed/blocked
    """
    tasks = breakdown.get("tasks", [])

    # Filter for pending tasks
    pending_tasks = [t for t in tasks if t.get("status") == "pending"]

    if not pending_tasks:
        return None

    # Find tasks with no pending dependencies
    ready_tasks = []
    for task in pending_tasks:
        deps = task.get("dependencies", [])
        all_deps_done = True

        for dep_id in deps:
            # Find the dependency task
            dep_task = next((t for t in tasks if t.get("task_id") == dep_id), None)
            if dep_task and dep_task.get("status") != "completed":
                all_deps_done = False
                break

        if all_deps_done:
            ready_tasks.append(task)

    if not ready_tasks:
        # All pending tasks are blocked
        return None

    # Sort by priority (critical > high > medium > low)
    priority_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    ready_tasks.sort(key=lambda t: priority_order.get(t.get("priority", "medium"), 2))

    return ready_tasks[0]
