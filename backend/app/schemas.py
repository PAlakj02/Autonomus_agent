from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class CreateRequestIn(BaseModel):
    raw_request: str


class ClarifyIn(BaseModel):
    answer: str


class DesignRequestOut(BaseModel):
    id: str
    student_id: str
    status: str
    raw_request: str
    structured_info: dict[str, Any] | None = None
    clarification_question: str | None = None
    clarification_answer: str | None = None
    workflow_type: str | None = None
    moderation_request_result: dict[str, Any] | None = None
    moderation_output_result: dict[str, Any] | None = None
    rejection_reason: str | None = None
    output_type: str | None = None
    preview_url: str | None = None
    final_url: str | None = None
    confirmed: bool
    print_url: str | None = None
    page_count: int | None = None
    error_message: str | None = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


# ---- Mock paper flow ----


class MaterialOut(BaseModel):
    id: str
    filename: str
    content_type: str | None = None
    kind: str
    file_url: str
    char_count: int
    page_count: int | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


class CreatePaperIn(BaseModel):
    subject: str = Field(min_length=1, max_length=120)
    title: str | None = Field(default=None, max_length=200)
    grade: str | None = Field(default=None, max_length=40)
    board: str | None = Field(default=None, max_length=60)
    total_marks: int = Field(default=50, ge=10, le=200)
    duration_minutes: int = Field(default=90, ge=15, le=240)
    difficulty: str = Field(default="mixed", pattern="^(easy|medium|hard|mixed)$")
    instructions: str | None = Field(default=None, max_length=2000)
    material_ids: list[str] = Field(default_factory=list, max_length=10)


class MockPaperOut(BaseModel):
    id: str
    status: str
    title: str
    subject: str
    grade: str | None = None
    board: str | None = None
    total_marks: int
    duration_minutes: int
    difficulty: str
    instructions: str | None = None
    material_ids: list[str]
    blueprint: dict[str, Any] | None = None
    questions: list[dict[str, Any]] | None = None
    verification: dict[str, Any] | None = None
    source: str | None = None
    paper_url: str | None = None
    answer_key_url: str | None = None
    page_count: int | None = None
    answer_key_page_count: int | None = None
    error_message: str | None = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


# ---- Print queue ----


class CreatePrintJobIn(BaseModel):
    source_type: str = Field(pattern="^(design|paper|answer_key)$")
    source_id: str
    destination: str = Field(default="device", pattern="^(device|shared)$")
    copies: int = Field(default=1, ge=1)
    color: bool = False
    duplex: bool = False


class PrintJobOut(BaseModel):
    id: str
    source_type: str
    source_id: str
    destination: str
    title: str
    file_url: str
    pages: int
    copies: int
    total_pages: int
    color: bool
    duplex: bool
    paper_size: str
    status: str
    printer_backend: str | None = None
    printer_name: str | None = None
    error_message: str | None = None
    created_at: datetime
    confirmed_at: datetime | None = None
    completed_at: datetime | None = None

    model_config = {"from_attributes": True}


class PrintQuotaOut(BaseModel):
    daily_limit: int
    used_today: int
    reserved: int  # queued/printing jobs not yet finished, already counted in used_today
    remaining: int
    max_pages_per_job: int
    max_copies: int


class PrinterStatusOut(BaseModel):
    shared_available: bool
    backend: str
    printer_name: str | None = None
    online: bool
    detail: str
    queued_jobs: int


class AgentJobUpdateIn(BaseModel):
    status: str = Field(pattern="^(printing|completed|failed)$")
    error_message: str | None = None
    external_job_id: str | None = None
