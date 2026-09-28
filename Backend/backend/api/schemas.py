"""Pydantic request/response models shared across routers."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field


# -------------------------------------------------------------------
# Audio / chat / TTS
# -------------------------------------------------------------------


class AudioChunk(BaseModel):
    audio_b64: str
    mime_type: str | None = "audio/webm"


class ChatRequest(BaseModel):
    message: str
    from_creator: bool = True
    conversation_id: Optional[int] = None
    regenerate: bool = False
    # "voice" when the user said it out loud (live mic): she answers like
    # spoken conversation. Default "text".
    modality: str = "text"


class ChatResponse(BaseModel):
    ok: bool
    reply: str
    emotion: Optional[str] = None
    emotion_intensity: Optional[float] = None
    affinity_to_creator: Optional[float] = None
    error: Optional[str] = None
    user_message_id: Optional[int] = None
    assistant_message_id: Optional[int] = None
    tokens_used: Optional[int] = None
    token_budget: Optional[int] = None


class TTSRequest(BaseModel):
    text: str
    length_scale: Optional[float] = 1.0
    noise_scale: Optional[float] = 0.6
    noise_w: Optional[float] = 0.8
    voice: Optional[str] = None


class STTRequest(BaseModel):
    audio_b64: str
    mime_type: Optional[str] = "audio/webm"


# Inline duplicate of STTRequest from server.py (api_stt route).
# Kept as an alias until the route is extracted.
STTRequest2 = STTRequest


# -------------------------------------------------------------------
# Screen / vision
# -------------------------------------------------------------------


class ScreenRecordingStartResponse(BaseModel):
    ok: bool
    error: Optional[str] = None


class ScreenRecordingStopResponse(BaseModel):
    ok: bool
    error: Optional[str] = None
    recording_path: Optional[str] = None


class ScreenshotAnalysisResponse(BaseModel):
    ok: bool
    error: Optional[str] = None
    description: Optional[str] = None
    suggestions: Optional[str] = None


class VisionRequest(BaseModel):
    prompt: str
    image: str  # base64 encoded image


class VisionResponse(BaseModel):
    description: str


class VideoSummaryRequest(BaseModel):
    prompt: str
    frames: List[str]  # list of base64 encoded images


class VideoSummaryResponse(BaseModel):
    summary: str


class SmartAnalysisRequest(BaseModel):
    image: str  # base64 encoded image
    mode: Optional[str] = "general"  # "error_detection", "ui_analysis", "code_recognition", "general"
    auto_detect: bool = True


class SmartAnalysisResponse(BaseModel):
    mode: str
    analysis: Dict[str, Any]
    formatted: str


# -------------------------------------------------------------------
# Settings / system
# -------------------------------------------------------------------


class SettingUpdate(BaseModel):
    key: str
    value: str


class TimezoneUpdate(BaseModel):
    timezone: str  # IANA timezone string (e.g., "America/New_York")


# -------------------------------------------------------------------
# Conversations / messages / mood
# -------------------------------------------------------------------


class ConversationCreate(BaseModel):
    title: Optional[str] = None


class MessageCreate(BaseModel):
    role: str
    content: str
    meta_json: Optional[Dict[str, Any]] = None


class MessageUpdate(BaseModel):
    content: str


class PinMessage(BaseModel):
    pinned: bool = True


class ConversationRename(BaseModel):
    title: str


class ConversationTag(BaseModel):
    conversation_id: int


class MoodUpdate(BaseModel):
    emotion: Optional[str] = None
    intensity: Optional[float] = None
    affinity: Optional[float] = None
    manual_override: Optional[bool] = None


# -------------------------------------------------------------------
# Memories / skills
# -------------------------------------------------------------------


class MemoryCreate(BaseModel):
    role: str
    content: str
    tags: str | None = ""
    importance: int | None = 0


class SkillRegister(BaseModel):
    name: str
    slug: str
    description: Optional[str] = ""
    enabled: Optional[bool] = True
    config: Optional[Dict[str, Any]] = None


class SkillInstallUrlRequest(BaseModel):
    url: str
    enabled: Optional[bool] = None
    overwrite: bool = False


class IdentityUpdate(BaseModel):
    name: Optional[str] = ""
    call_name: Optional[str] = ""
    pronouns: Optional[str] = ""
    timezone: Optional[str] = ""
    notes: Optional[str] = ""


class PersonaSwitchRequest(BaseModel):
    slug: str


# -------------------------------------------------------------------
# Projects / files
# -------------------------------------------------------------------


class ProjectCreate(BaseModel):
    name: str
    description: Optional[str] = None
    root_path: Optional[str] = None


class ProjectRename(BaseModel):
    name: str


class ProjectFileUpload(BaseModel):
    file_name: str
    file_path: str
    content: str
    file_type: Optional[str] = None
    file_size: Optional[int] = None


class FileCreate(BaseModel):
    file_name: str
    content: Optional[str] = ""
    file_path: Optional[str] = None


class FileUpdate(BaseModel):
    content: str


class FileRename(BaseModel):
    new_name: str


# -------------------------------------------------------------------
# Git
# -------------------------------------------------------------------


class GitCommitPayload(BaseModel):
    message: str


class GitPushPayload(BaseModel):
    remote: str = "origin"
    branch: Optional[str] = None


class GitPullPayload(BaseModel):
    remote: str = "origin"
    branch: Optional[str] = None


class GitStashPayload(BaseModel):
    action: str = "push"  # push, pop, list, clear
    message: Optional[str] = None


class GitCheckoutPayload(BaseModel):
    branch: str
    create: bool = False


# -------------------------------------------------------------------
# Task breakdown
# -------------------------------------------------------------------


class TaskBreakdownRequest(BaseModel):
    user_request: str
    context: Optional[str] = None
    use_ai: bool = True


class TaskBreakdownResponse(BaseModel):
    main_goal: str
    estimated_total_time: str
    complexity: int
    tasks: List[Dict[str, Any]]
    critical_path: List[str]
    risks: List[str]
    suggestions: List[str]
    breakdown_method: str
    formatted: str


class TaskUpdateRequest(BaseModel):
    breakdown: Dict[str, Any]
    task_id: str
    status: str  # "pending", "in_progress", "completed", "blocked", "cancelled"
    notes: Optional[str] = None


class NextTaskResponse(BaseModel):
    has_next: bool
    task: Optional[Dict[str, Any]]


# -------------------------------------------------------------------
# Multi-file edit / boilerplate
# -------------------------------------------------------------------


class MultiFileEditRequest(BaseModel):
    user_request: str
    project_root: Optional[str] = None
    existing_files: Optional[List[str]] = None
    use_ai: bool = True


class MultiFileEditResponse(BaseModel):
    description: str
    changes: List[Dict[str, Any]]
    execution_order: List[str]
    risks: List[str]
    validation_steps: List[str]
    formatted: str


class BoilerplateRequest(BaseModel):
    file_type: str  # "python_class", "react_component", etc.
    name: str
    description: Optional[str] = None
    extra_params: Optional[Dict[str, Any]] = None


class BoilerplateResponse(BaseModel):
    file_path: str
    content: str


# -------------------------------------------------------------------
# Test generation / execution
# -------------------------------------------------------------------


class TestGenerationRequest(BaseModel):
    code: str
    language: str = "python"
    test_type: str = "unit"
    framework: Optional[str] = None
    use_ai: bool = True


class TestGenerationResponse(BaseModel):
    test_file_content: str
    test_suite_name: str
    num_tests: int
    framework: str
    formatted: str


class TestExecutionRequest(BaseModel):
    test_file_path: str
    framework: str = "pytest"
    with_coverage: bool = False
    timeout: int = Field(60, ge=1, le=900)


class TestExecutionResponse(BaseModel):
    success: bool
    total_tests: int
    passed_tests: int
    failed_tests: int
    skipped_tests: int
    duration_seconds: float
    coverage_percent: Optional[float]
    formatted: str


# -------------------------------------------------------------------
# Sandbox execution
# -------------------------------------------------------------------


class SandboxExecutionRequest(BaseModel):
    code: str
    language: str = "python"
    input_files: Optional[Dict[str, str]] = None
    timeout: int = Field(30, ge=1, le=120)
    allow_network: bool = False


class SandboxExecutionResponse(BaseModel):
    success: bool
    output: str
    error: str
    exit_code: int
    execution_time: float
    files_created: List[str]
    security_violations: List[str]
    formatted: str


# -------------------------------------------------------------------
# Code delivery
# -------------------------------------------------------------------


class CodeChangeRequest(BaseModel):
    file_path: str
    change_type: str  # "create", "edit", "delete", "rename"
    old_content: Optional[str] = None
    new_content: Optional[str] = None
    old_path: Optional[str] = None
    description: str = ""
    language: str = "python"


class CodeDeliveryRequest(BaseModel):
    # `validate` is a reserved BaseModel method name; keep the wire/JSON key as
    # "validate" via an alias while exposing it as `validate_changes` in Python.
    model_config = ConfigDict(populate_by_name=True)

    changes: List[CodeChangeRequest]
    summary: str
    format: str = "diff"  # "diff", "inline", "split_view"
    validate_changes: bool = Field(default=True, alias="validate")
    auto_format: bool = False


class CodeDeliveryResponse(BaseModel):
    delivery_id: str
    summary: str
    total_lines_added: int
    total_lines_removed: int
    files_affected: int
    validation_passed: bool
    can_auto_apply: bool
    warnings: List[str]
    formatted: str
    changes: List[Dict[str, Any]]


class ApplyCodeRequest(BaseModel):
    delivery_id: str
    changes: List[Dict[str, Any]]
    create_backups: bool = True
    dry_run: bool = False


class ApplyCodeResponse(BaseModel):
    success: bool
    applied: List[str]
    failed: List[str]
    skipped: List[str]
    message: str


# -------------------------------------------------------------------
# Ollama vision / text
# -------------------------------------------------------------------


class OllamaVisionRequest(BaseModel):
    mode: str = "general"  # general, ocr, ui, code
    model: str = "qwen3-vl:8b"
    custom_prompt: Optional[str] = None


class OllamaTextAnalysisRequest(BaseModel):
    text: str
    mode: str = "code"
    language: str = "auto"
