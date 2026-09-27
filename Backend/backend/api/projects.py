"""Project + file management endpoints (non-git)."""
from __future__ import annotations

import logging
import os
from typing import List, Optional

from fastapi import APIRouter, HTTPException

from backend.api.schemas import (
    ConversationTag,
    FileCreate,
    FileRename,
    FileUpdate,
    ProjectCreate,
    ProjectFileUpload,
    ProjectRename,
)
from backend.models.projects import (
    add_file_to_project,
    build_file_tree,
    create_file_in_project,
    create_project,
    delete_file,
    delete_project,
    detect_project_metadata,
    get_conversation_projects,
    get_current_file,
    get_file_content,
    get_project,
    get_project_context,
    get_project_conversations,
    get_project_files,
    list_projects,
    rename_file,
    rename_project,
    set_current_file,
    tag_conversation_to_project,
    untag_conversation_from_project,
    update_file_content,
)
from backend.project_analysis import analyze_project_full

router = APIRouter()


@router.get("/api/projects")
def api_list_projects(limit: int = 100):
    """List all projects with file counts."""
    try:
        projects = list_projects(limit=limit)
        return {"ok": True, "projects": projects}
    except Exception as e:
        logging.exception("[/api/projects GET] failed")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/projects")
def api_create_project(payload: ProjectCreate):
    """Create a new project with optional metadata detection."""
    try:
        project_id = create_project(payload.name, payload.description, payload.root_path)
        return {"ok": True, "project_id": project_id}
    except Exception as e:
        logging.exception("[/api/projects POST] failed")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/projects/{project_id}")
def api_get_project(project_id: int):
    """Get details of a single project."""
    try:
        project = get_project(project_id)
        if not project:
            raise HTTPException(status_code=404, detail="Project not found")
        return {"ok": True, "project": project}
    except HTTPException:
        raise
    except Exception as e:
        logging.exception(f"[/api/projects/{project_id} GET] failed")
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/api/projects/{project_id}")
def api_delete_project(project_id: int):
    """Delete a project and all its files."""
    try:
        delete_project(project_id)
        return {"ok": True, "deleted_id": project_id}
    except Exception as e:
        logging.exception(f"[/api/projects/{project_id} DELETE] failed")
        raise HTTPException(status_code=500, detail=str(e))


@router.patch("/api/projects/{project_id}/rename")
def api_rename_project(project_id: int, payload: ProjectRename):
    """Rename a project."""
    try:
        success = rename_project(project_id, payload.name)
        if success:
            return {"ok": True, "project_id": project_id, "new_name": payload.name}
        raise HTTPException(status_code=404, detail="Project not found")
    except HTTPException:
        raise
    except Exception as e:
        logging.exception(f"[/api/projects/{project_id}/rename PATCH] failed")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/projects/{project_id}/files")
def api_get_project_files(project_id: int):
    """Get all files in a project."""
    try:
        files = get_project_files(project_id)
        return {"ok": True, "files": files}
    except Exception as e:
        logging.exception(f"[/api/projects/{project_id}/files GET] failed")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/projects/{project_id}/files")
def api_upload_file_to_project(project_id: int, payload: ProjectFileUpload):
    """Upload a file to a project."""
    try:
        file_id = add_file_to_project(
            project_id=project_id,
            file_name=payload.file_name,
            file_path=payload.file_path,
            content=payload.content,
            file_type=payload.file_type,
            file_size=payload.file_size,
        )
        return {"ok": True, "file_id": file_id}
    except Exception as e:
        logging.exception(f"[/api/projects/{project_id}/files POST] failed")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/projects/{project_id}/files/{file_id}")
def api_get_file_content(project_id: int, file_id: int):
    """Get the content of a specific file."""
    try:
        content = get_file_content(file_id)
        if content is None:
            raise HTTPException(status_code=404, detail="File not found")
        return {"ok": True, "content": content}
    except HTTPException:
        raise
    except Exception as e:
        logging.exception(f"[/api/projects/{project_id}/files/{file_id} GET] failed")
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/api/projects/{project_id}/files/{file_id}")
def api_delete_file(project_id: int, file_id: int):
    """Delete a file from a project."""
    try:
        delete_file(file_id)
        return {"ok": True, "deleted_id": file_id}
    except Exception as e:
        logging.exception(f"[/api/projects/{project_id}/files/{file_id} DELETE] failed")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/projects/{project_id}/context")
def api_get_project_context(project_id: int, max_files: int = 50):
    """Get a text summary of all files in a project for AI context."""
    try:
        context = get_project_context(project_id, max_files=max_files)
        return {"ok": True, "context": context}
    except Exception as e:
        logging.exception(f"[/api/projects/{project_id}/context GET] failed")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/projects/scan")
def api_scan_project_folder(root_path: str):
    """Scan a folder and detect project metadata."""
    try:
        if not os.path.exists(root_path):
            raise HTTPException(status_code=404, detail="Path not found")
        metadata = detect_project_metadata(root_path)
        file_tree = build_file_tree(root_path, max_depth=3)
        return {"ok": True, "metadata": metadata, "file_tree": file_tree}
    except HTTPException:
        raise
    except Exception as e:
        logging.exception("[/api/projects/scan POST] failed")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/projects/{project_id}/tree")
def api_get_file_tree(project_id: int):
    """Get file tree structure for a project."""
    try:
        project = get_project(project_id)
        if not project:
            raise HTTPException(status_code=404, detail="Project not found")
        root_path = project.get("root_path")
        if not root_path or not os.path.exists(root_path):
            return {"ok": False, "error": "Project root path not found"}
        file_tree = build_file_tree(root_path, max_depth=4)
        return {"ok": True, "tree": file_tree}
    except HTTPException:
        raise
    except Exception as e:
        logging.exception(f"[/api/projects/{project_id}/tree GET] failed")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/projects/{project_id}/analyze")
def api_analyze_project(project_id: int):
    """Comprehensive project analysis (deps, README, git history, entry points)."""
    try:
        analysis = analyze_project_full(project_id)
        if "error" in analysis:
            raise HTTPException(status_code=404, detail=analysis["error"])
        return {"ok": True, "analysis": analysis}
    except HTTPException:
        raise
    except Exception as e:
        logging.exception(f"[/api/projects/{project_id}/analyze GET] failed")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/projects/{project_id}/current_file/{file_id}")
def api_set_current_file(project_id: int, file_id: int):
    """Set the current working file for a project."""
    try:
        set_current_file(project_id, file_id)
        return {"ok": True}
    except Exception as e:
        logging.exception(f"[/api/projects/{project_id}/current_file/{file_id} POST] failed")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/projects/{project_id}/current_file")
def api_get_current_file(project_id: int):
    """Get the current working file for a project."""
    try:
        file = get_current_file(project_id)
        if not file:
            return {"ok": False, "error": "No current file set"}
        return {"ok": True, "file": file}
    except Exception as e:
        logging.exception(f"[/api/projects/{project_id}/current_file GET] failed")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/projects/{project_id}/files/create")
def api_create_file(project_id: int, payload: FileCreate):
    """Create a new file in a project."""
    try:
        file_id = create_file_in_project(
            project_id=project_id,
            file_name=payload.file_name,
            content=payload.content or "",
            file_path=payload.file_path,
        )
        return {"ok": True, "file_id": file_id}
    except Exception as e:
        logging.exception(f"[/api/projects/{project_id}/files/create POST] failed")
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/api/projects/{project_id}/files/{file_id}")
def api_update_file(project_id: int, file_id: int, payload: FileUpdate):
    """Update file content."""
    try:
        update_file_content(file_id, payload.content)
        return {"ok": True, "file_id": file_id}
    except Exception as e:
        logging.exception(f"[/api/projects/{project_id}/files/{file_id} PUT] failed")
        raise HTTPException(status_code=500, detail=str(e))


@router.patch("/api/projects/{project_id}/files/{file_id}/rename")
def api_rename_file(project_id: int, file_id: int, payload: FileRename):
    """Rename a file."""
    try:
        rename_file(file_id, payload.new_name)
        return {"ok": True, "file_id": file_id}
    except Exception as e:
        logging.exception(f"[/api/projects/{project_id}/files/{file_id}/rename PATCH] failed")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/projects/{project_id}/conversations")
def api_get_project_conversations(project_id: int):
    """Get all conversations tagged to a project."""
    try:
        conversations = get_project_conversations(project_id)
        return {"ok": True, "conversations": conversations}
    except Exception as e:
        logging.exception(f"[/api/projects/{project_id}/conversations GET] failed")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/projects/{project_id}/conversations/tag")
def api_tag_conversation(project_id: int, payload: ConversationTag):
    """Tag a conversation to a project."""
    try:
        tag_conversation_to_project(project_id, payload.conversation_id)
        return {"ok": True}
    except Exception as e:
        logging.exception(f"[/api/projects/{project_id}/conversations/tag POST] failed")
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/api/projects/{project_id}/conversations/{conversation_id}")
def api_untag_conversation(project_id: int, conversation_id: int):
    """Remove conversation tag from a project."""
    try:
        untag_conversation_from_project(project_id, conversation_id)
        return {"ok": True}
    except Exception as e:
        logging.exception(
            f"[/api/projects/{project_id}/conversations/{conversation_id} DELETE] failed"
        )
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/conversations/{conversation_id}/projects")
def api_get_conversation_projects(conversation_id: int):
    """Get all projects a conversation is tagged to."""
    try:
        projects = get_conversation_projects(conversation_id)
        return {"ok": True, "projects": projects}
    except Exception as e:
        logging.exception(f"[/api/conversations/{conversation_id}/projects GET] failed")
        raise HTTPException(status_code=500, detail=str(e))
