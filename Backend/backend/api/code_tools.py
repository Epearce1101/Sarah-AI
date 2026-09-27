"""Code-tooling endpoints: task breakdown, multi-file edit, test generation,
sandbox execution, and code delivery."""
from __future__ import annotations

import hashlib
import logging
import os
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException

from backend.db import get_connection

from backend.api.schemas import (
    ApplyCodeRequest,
    ApplyCodeResponse,
    BoilerplateRequest,
    BoilerplateResponse,
    CodeDeliveryRequest,
    CodeDeliveryResponse,
    MultiFileEditRequest,
    MultiFileEditResponse,
    NextTaskResponse,
    SandboxExecutionRequest,
    SandboxExecutionResponse,
    TaskBreakdownRequest,
    TaskBreakdownResponse,
    TestExecutionRequest,
    TestExecutionResponse,
    TestGenerationRequest,
    TestGenerationResponse,
)

# ---------------------------------------------------------------------------
# Optional feature imports
# ---------------------------------------------------------------------------

try:
    from backend.task_breakdown import breakdown_task, format_task_breakdown, get_next_task
    TASK_BREAKDOWN_ENABLED = True
    TASK_BREAKDOWN_IMPORT_ERROR: Optional[str] = None
except Exception as e:
    TASK_BREAKDOWN_ENABLED = False
    TASK_BREAKDOWN_IMPORT_ERROR = str(e)
    breakdown_task = format_task_breakdown = get_next_task = None

try:
    from backend.multi_file_editor import (
        FileType,
        format_multi_file_edit,
        generate_boilerplate,
        plan_multi_file_edit,
    )
    MULTI_FILE_EDIT_ENABLED = True
    MULTI_FILE_EDIT_IMPORT_ERROR: Optional[str] = None
except Exception as e:
    MULTI_FILE_EDIT_ENABLED = False
    MULTI_FILE_EDIT_IMPORT_ERROR = str(e)
    plan_multi_file_edit = format_multi_file_edit = generate_boilerplate = None
    FileType = None

try:
    from backend.test_generation import (
        TestFramework,
        TestType,
        format_test_result,
        format_test_suite,
        generate_tests,
        run_tests_in_sandbox,
    )
    TEST_GENERATION_ENABLED = True
    TEST_GENERATION_IMPORT_ERROR: Optional[str] = None
except Exception as e:
    TEST_GENERATION_ENABLED = False
    TEST_GENERATION_IMPORT_ERROR = str(e)
    generate_tests = format_test_suite = run_tests_in_sandbox = format_test_result = None
    TestType = TestFramework = None

try:
    from backend.sandbox import execute_code_safely, format_sandbox_result
    SANDBOX_ENABLED = True
    SANDBOX_IMPORT_ERROR: Optional[str] = None
except Exception as e:
    SANDBOX_ENABLED = False
    SANDBOX_IMPORT_ERROR = str(e)
    execute_code_safely = format_sandbox_result = None

try:
    from backend.code_delivery import (
        ChangeType,
        CodeChange,
        DeliveryFormat,
        apply_delivery,
        create_code_delivery,
        format_code_delivery,
        generate_unified_diff,
    )
    CODE_DELIVERY_ENABLED = True
    CODE_DELIVERY_IMPORT_ERROR: Optional[str] = None
except Exception as e:
    CODE_DELIVERY_ENABLED = False
    CODE_DELIVERY_IMPORT_ERROR = str(e)
    create_code_delivery = format_code_delivery = apply_delivery = None
    generate_unified_diff = None
    CodeChange = DeliveryFormat = ChangeType = None


router = APIRouter()


def _project_roots() -> List[str]:
    """Real paths of every project with a root folder on disk."""
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT root_path FROM projects WHERE root_path IS NOT NULL AND root_path != ''"
        ).fetchall()
    finally:
        conn.close()
    roots = []
    for row in rows:
        path = os.path.realpath(row["root_path"])
        if os.path.isdir(path):
            roots.append(path)
    return roots


def _inside_any_root(path: str, roots: List[str]) -> bool:
    if not os.path.isabs(path):
        return False
    target = os.path.normcase(os.path.realpath(path))
    for root in roots:
        root_norm = os.path.normcase(root)
        try:
            if os.path.commonpath([target, root_norm]) == root_norm:
                return True
        except ValueError:  # different drives
            continue
    return False


# ---------------------------------------------------------------------------
# Task breakdown
# ---------------------------------------------------------------------------


@router.post("/api/task-breakdown", response_model=TaskBreakdownResponse)
def api_task_breakdown(payload: TaskBreakdownRequest):
    """Break down a complex task into subtasks with dependencies and priorities."""
    if not TASK_BREAKDOWN_ENABLED or breakdown_task is None:
        raise HTTPException(
            status_code=503,
            detail=f"Task Breakdown is disabled: {TASK_BREAKDOWN_IMPORT_ERROR}",
        )

    try:
        breakdown = breakdown_task(
            user_request=payload.user_request,
            context=payload.context,
            use_ai=payload.use_ai,
        )
        formatted = format_task_breakdown(breakdown, include_details=True)
        return TaskBreakdownResponse(**breakdown, formatted=formatted)
    except Exception as e:
        logging.exception("[/api/task-breakdown] Task breakdown request failed")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/task-next", response_model=NextTaskResponse)
def api_task_next(breakdown: Dict[str, Any]):
    """Return the next task that should be worked on based on deps + priorities."""
    if not TASK_BREAKDOWN_ENABLED or get_next_task is None:
        raise HTTPException(
            status_code=503,
            detail=f"Task Breakdown is disabled: {TASK_BREAKDOWN_IMPORT_ERROR}",
        )
    try:
        next_task = get_next_task(breakdown)
        return NextTaskResponse(has_next=next_task is not None, task=next_task)
    except Exception as e:
        logging.exception("[/api/task-next] Get next task request failed")
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# Multi-file edit / boilerplate
# ---------------------------------------------------------------------------


@router.post("/api/multi-file-edit", response_model=MultiFileEditResponse)
def api_multi_file_edit(payload: MultiFileEditRequest):
    """Plan a coordinated multi-file edit operation."""
    if not MULTI_FILE_EDIT_ENABLED or plan_multi_file_edit is None:
        raise HTTPException(
            status_code=503,
            detail=f"Multi-File Edit is disabled: {MULTI_FILE_EDIT_IMPORT_ERROR}",
        )

    try:
        edit_plan = plan_multi_file_edit(
            user_request=payload.user_request,
            project_root=payload.project_root,
            existing_files=payload.existing_files,
            use_ai=payload.use_ai,
        )
        formatted = format_multi_file_edit(edit_plan, include_content=False)

        changes_dicts = []
        for change in edit_plan.changes:
            changes_dicts.append({
                "operation": change.operation,
                "file_path": change.file_path,
                "content": change.content,
                "old_path": change.old_path,
                "reason": change.reason,
                "dependencies": change.dependencies,
                "imports_added": change.imports_added,
                "imports_removed": change.imports_removed,
            })

        return MultiFileEditResponse(
            description=edit_plan.description,
            changes=changes_dicts,
            execution_order=edit_plan.execution_order,
            risks=edit_plan.risks,
            validation_steps=edit_plan.validation_steps,
            formatted=formatted,
        )
    except Exception as e:
        logging.exception("[/api/multi-file-edit] Multi-file edit failed")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/generate-boilerplate", response_model=BoilerplateResponse)
def api_generate_boilerplate(payload: BoilerplateRequest):
    """Generate boilerplate code for common file types."""
    if not MULTI_FILE_EDIT_ENABLED or generate_boilerplate is None:
        raise HTTPException(
            status_code=503,
            detail=f"Boilerplate Generation is disabled: {MULTI_FILE_EDIT_IMPORT_ERROR}",
        )

    try:
        try:
            file_type = FileType(payload.file_type)
        except ValueError:
            raise HTTPException(
                status_code=400, detail=f"Invalid file_type: {payload.file_type}"
            )

        params = payload.extra_params or {}
        params["name"] = payload.name
        params["description"] = payload.description or f"{payload.name} module"

        if file_type == FileType.PYTHON_CLASS:
            file_path = f"{payload.name.lower()}.py"
            params["class_name"] = payload.name
            params["class_description"] = params["description"]
        elif file_type == FileType.REACT_COMPONENT:
            file_path = f"{payload.name}.jsx"
            params["component_name"] = payload.name
            params["component_name_lower"] = payload.name.lower()
        elif file_type == FileType.API_ROUTE:
            file_path = f"routes/{payload.name.lower()}.py"
            params["model_name"] = payload.name
            params["route_name"] = payload.name.lower()
            params["route_path"] = f"/api/{payload.name.lower()}"
        elif file_type == FileType.TEST_FILE:
            file_path = f"test_{payload.name.lower()}.py"
            params["module_name"] = payload.name
            params["function_name"] = payload.name.lower()
            params["class_name"] = payload.name
            params["module_path"] = payload.name.lower()
        elif file_type == FileType.CONFIG_FILE:
            file_path = f"config/{payload.name.lower()}.json"
            params["config_name"] = payload.name
        else:
            file_path = f"{payload.name.lower()}.py"

        content = generate_boilerplate(file_type, **params)
        return BoilerplateResponse(file_path=file_path, content=content)
    except HTTPException:
        raise
    except Exception as e:
        logging.exception("[/api/generate-boilerplate] Boilerplate generation failed")
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# Test generation / execution
# ---------------------------------------------------------------------------


@router.post("/api/generate-tests", response_model=TestGenerationResponse)
def api_generate_tests(payload: TestGenerationRequest):
    """Generate comprehensive tests for code using AI."""
    if not TEST_GENERATION_ENABLED or generate_tests is None:
        raise HTTPException(
            status_code=503,
            detail=f"Test Generation is disabled: {TEST_GENERATION_IMPORT_ERROR}",
        )

    try:
        try:
            test_type = TestType(payload.test_type)
        except ValueError:
            test_type = TestType.UNIT

        framework = None
        if payload.framework:
            try:
                framework = TestFramework(payload.framework)
            except ValueError:
                pass

        test_suite = generate_tests(
            code=payload.code,
            language=payload.language,
            test_type=test_type,
            framework=framework,
            use_ai=payload.use_ai,
        )

        if not test_suite:
            raise HTTPException(status_code=500, detail="Failed to generate tests")

        test_content = format_test_suite(test_suite)
        return TestGenerationResponse(
            test_file_content=test_content,
            test_suite_name=test_suite.module_name,
            num_tests=len(test_suite.test_cases),
            framework=str(test_suite.framework),
            formatted=f"Generated {len(test_suite.test_cases)} tests for {test_suite.module_name}",
        )
    except HTTPException:
        raise
    except Exception as e:
        logging.exception("[/api/generate-tests] Test generation failed")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/run-tests", response_model=TestExecutionResponse)
def api_run_tests(payload: TestExecutionRequest):
    """Run tests in a sandboxed environment."""
    if not TEST_GENERATION_ENABLED or run_tests_in_sandbox is None:
        raise HTTPException(
            status_code=503,
            detail=f"Test Execution is disabled: {TEST_GENERATION_IMPORT_ERROR}",
        )

    try:
        try:
            framework = TestFramework(payload.framework)
        except ValueError:
            framework = TestFramework.PYTEST

        result = run_tests_in_sandbox(
            test_file_path=payload.test_file_path,
            framework=framework,
            timeout=payload.timeout,
            with_coverage=payload.with_coverage,
        )
        formatted = format_test_result(result)

        return TestExecutionResponse(
            success=result.success,
            total_tests=result.total_tests,
            passed_tests=result.passed_tests,
            failed_tests=result.failed_tests,
            skipped_tests=result.skipped_tests,
            duration_seconds=result.duration_seconds,
            coverage_percent=result.coverage_percent,
            formatted=formatted,
        )
    except Exception as e:
        logging.exception("[/api/run-tests] Test execution failed")
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# Sandbox execution
# ---------------------------------------------------------------------------


@router.post("/api/sandbox-execute", response_model=SandboxExecutionResponse)
def api_sandbox_execute(payload: SandboxExecutionRequest):
    """Execute code safely in a sandboxed environment."""
    if not SANDBOX_ENABLED or execute_code_safely is None:
        raise HTTPException(
            status_code=503, detail=f"Sandbox is disabled: {SANDBOX_IMPORT_ERROR}"
        )

    try:
        result = execute_code_safely(
            code=payload.code,
            language=payload.language,
            timeout=payload.timeout,
            allow_network=payload.allow_network,
            input_files=payload.input_files,
        )
        formatted = format_sandbox_result(result)
        return SandboxExecutionResponse(
            success=result.success,
            output=result.output,
            error=result.error,
            exit_code=result.exit_code,
            execution_time=result.execution_time,
            files_created=result.files_created,
            security_violations=result.security_violations,
            formatted=formatted,
        )
    except Exception as e:
        logging.exception("[/api/sandbox-execute] Sandbox execution failed")
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# Code delivery
# ---------------------------------------------------------------------------


@router.post("/api/code-delivery", response_model=CodeDeliveryResponse)
def api_code_delivery(payload: CodeDeliveryRequest):
    """Create a code delivery package with validation and formatting."""
    if not CODE_DELIVERY_ENABLED or create_code_delivery is None:
        raise HTTPException(
            status_code=503,
            detail=f"Code Delivery is disabled: {CODE_DELIVERY_IMPORT_ERROR}",
        )

    try:
        changes = []
        for i, change_req in enumerate(payload.changes):
            change_id = hashlib.md5(
                f"{change_req.file_path}_{i}".encode()
            ).hexdigest()[:8]
            change = CodeChange(
                change_id=change_id,
                file_path=change_req.file_path,
                change_type=ChangeType(change_req.change_type),
                old_content=change_req.old_content,
                new_content=change_req.new_content,
                old_path=change_req.old_path,
                description=change_req.description,
                language=change_req.language,
            )
            changes.append(change)

        try:
            delivery_format = DeliveryFormat(payload.format)
        except ValueError:
            delivery_format = DeliveryFormat.DIFF

        delivery = create_code_delivery(
            changes=changes,
            summary=payload.summary,
            format=delivery_format,
            validate=payload.validate_changes,
            auto_format=payload.auto_format,
        )
        formatted = format_code_delivery(
            delivery, include_diffs=True, include_full_content=False
        )

        changes_dicts = []
        for change in delivery.changes:
            changes_dicts.append({
                "change_id": change.change_id,
                "file_path": change.file_path,
                "change_type": change.change_type,
                "old_content": change.old_content,
                "new_content": change.new_content,
                "description": change.description,
                "language": change.language,
                "validation_errors": change.validation_errors,
            })

        return CodeDeliveryResponse(
            delivery_id=delivery.delivery_id,
            summary=delivery.summary,
            total_lines_added=delivery.total_lines_added,
            total_lines_removed=delivery.total_lines_removed,
            files_affected=delivery.files_affected,
            validation_passed=delivery.validation_passed,
            can_auto_apply=delivery.can_auto_apply,
            warnings=delivery.warnings,
            formatted=formatted,
            changes=changes_dicts,
        )
    except Exception as e:
        logging.exception("[/api/code-delivery] Code delivery failed")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/apply-code", response_model=ApplyCodeResponse)
def api_apply_code(payload: ApplyCodeRequest):
    """Apply code changes from a delivery package."""
    if not CODE_DELIVERY_ENABLED or apply_delivery is None:
        raise HTTPException(
            status_code=503,
            detail=f"Code Delivery is disabled: {CODE_DELIVERY_IMPORT_ERROR}",
        )

    # Writes, deletes and renames must land inside a registered project's
    # root folder; anything else (including relative paths, which would
    # resolve against the backend's own cwd) is refused before touching disk.
    roots = _project_roots()
    outside = [
        path
        for change_dict in payload.changes
        for path in (change_dict.get("file_path"), change_dict.get("old_path"))
        if path and not _inside_any_root(path, roots)
    ]
    if outside:
        raise HTTPException(
            status_code=403,
            detail={
                "error": "paths outside every registered project root",
                "paths": outside[:20],
                "project_roots": roots,
            },
        )

    try:
        changes = []
        for change_dict in payload.changes:
            change = CodeChange(
                change_id=change_dict.get("change_id", "unknown"),
                file_path=change_dict["file_path"],
                change_type=ChangeType(change_dict["change_type"]),
                old_content=change_dict.get("old_content"),
                new_content=change_dict.get("new_content"),
                old_path=change_dict.get("old_path"),
                description=change_dict.get("description", ""),
                language=change_dict.get("language", "python"),
            )
            changes.append(change)

        delivery = create_code_delivery(
            changes=changes, summary="Apply code changes", validate=False
        )
        delivery.delivery_id = payload.delivery_id

        results = apply_delivery(
            delivery=delivery,
            create_backups=payload.create_backups,
            dry_run=payload.dry_run,
        )

        if results["success"]:
            message = f"Successfully applied {len(results['applied'])} changes"
            if payload.dry_run:
                message += " (dry run - no actual changes made)"
        else:
            message = (
                f"Applied {len(results['applied'])} changes, "
                f"{len(results['failed'])} failed"
            )

        return ApplyCodeResponse(
            success=results["success"],
            applied=results["applied"],
            failed=results["failed"],
            skipped=results["skipped"],
            message=message,
        )
    except Exception as e:
        logging.exception("[/api/apply-code] Code application failed")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/code-diff")
def api_code_diff(file_path: str, old_content: str, new_content: str):
    """Generate a unified diff between old and new content."""
    if not CODE_DELIVERY_ENABLED or generate_unified_diff is None:
        raise HTTPException(
            status_code=503,
            detail=f"Code Delivery is disabled: {CODE_DELIVERY_IMPORT_ERROR}",
        )

    try:
        diff = generate_unified_diff(
            old_content=old_content, new_content=new_content, file_path=file_path
        )
        return {"diff": diff, "file_path": file_path}
    except Exception as e:
        logging.exception("[/api/code-diff] Diff generation failed")
        raise HTTPException(status_code=500, detail=str(e))
