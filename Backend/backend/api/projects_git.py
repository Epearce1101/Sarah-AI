"""Project git operations endpoints."""
from __future__ import annotations

import logging
from typing import List, Optional

from fastapi import APIRouter, HTTPException

from backend.api.schemas import (
    GitCheckoutPayload,
    GitCommitPayload,
    GitPullPayload,
    GitPushPayload,
    GitStashPayload,
)
from backend.models.projects import (
    get_git_diff,
    get_git_status,
    get_recent_commits,
    git_add,
    git_branch_list,
    git_checkout,
    git_commit,
    git_pull,
    git_push,
    git_stash,
)

router = APIRouter()


@router.get("/api/projects/{project_id}/git/status")
def api_get_git_status(project_id: int):
    """Get git status for a project."""
    try:
        status = get_git_status(project_id)
        return status
    except Exception as e:
        logging.exception(f"[/api/projects/{project_id}/git/status GET] failed")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/projects/{project_id}/git/commits")
def api_get_recent_commits(project_id: int, limit: int = 10):
    """Get recent git commits for a project."""
    try:
        commits = get_recent_commits(project_id, limit=limit)
        return {"ok": True, "commits": commits}
    except Exception as e:
        logging.exception(f"[/api/projects/{project_id}/git/commits GET] failed")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/projects/{project_id}/git/diff")
def api_get_git_diff(project_id: int, file_path: Optional[str] = None):
    """Get git diff for a project or specific file."""
    try:
        diff = get_git_diff(project_id, file_path)
        return {"ok": True, "diff": diff}
    except Exception as e:
        logging.exception(f"[/api/projects/{project_id}/git/diff GET] failed")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/projects/{project_id}/git/add")
def api_git_add(project_id: int, files: Optional[List[str]] = None):
    """Stage files for commit. If files is None, stages all changes."""
    try:
        result = git_add(project_id, files)
        return result
    except Exception as e:
        logging.exception(f"[/api/projects/{project_id}/git/add POST] failed")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/projects/{project_id}/git/commit")
def api_git_commit(project_id: int, payload: GitCommitPayload):
    """Commit staged changes with a message."""
    try:
        result = git_commit(project_id, payload.message)
        return result
    except Exception as e:
        logging.exception(f"[/api/projects/{project_id}/git/commit POST] failed")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/projects/{project_id}/git/push")
def api_git_push(project_id: int, payload: GitPushPayload = GitPushPayload()):
    """Push commits to remote repository."""
    try:
        result = git_push(project_id, payload.remote, payload.branch)
        return result
    except Exception as e:
        logging.exception(f"[/api/projects/{project_id}/git/push POST] failed")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/projects/{project_id}/git/pull")
def api_git_pull(project_id: int, payload: GitPullPayload = GitPullPayload()):
    """Pull changes from remote repository."""
    try:
        result = git_pull(project_id, payload.remote, payload.branch)
        return result
    except Exception as e:
        logging.exception(f"[/api/projects/{project_id}/git/pull POST] failed")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/projects/{project_id}/git/stash")
def api_git_stash(project_id: int, payload: GitStashPayload = GitStashPayload()):
    """Stash or unstash changes."""
    try:
        result = git_stash(project_id, payload.action, payload.message)
        return result
    except Exception as e:
        logging.exception(f"[/api/projects/{project_id}/git/stash POST] failed")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/projects/{project_id}/git/branches")
def api_git_branches(project_id: int):
    """List all branches."""
    try:
        result = git_branch_list(project_id)
        return result
    except Exception as e:
        logging.exception(f"[/api/projects/{project_id}/git/branches GET] failed")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/projects/{project_id}/git/checkout")
def api_git_checkout(project_id: int, payload: GitCheckoutPayload):
    """Checkout or create a branch."""
    try:
        result = git_checkout(project_id, payload.branch, payload.create)
        return result
    except Exception as e:
        logging.exception(f"[/api/projects/{project_id}/git/checkout POST] failed")
        raise HTTPException(status_code=500, detail=str(e))
