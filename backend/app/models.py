import uuid
from datetime import datetime

from sqlalchemy import String, Text, DateTime, JSON, Boolean, Integer
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


def new_id() -> str:
    return uuid.uuid4().hex


class DesignRequest(Base):
    __tablename__ = "design_requests"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    student_id: Mapped[str] = mapped_column(String, index=True, default="demo-student")

    raw_request: Mapped[str] = mapped_column(Text)

    # pending_clarification | ready_to_generate | moderating_request |
    # generating | moderating_output | preview_ready | rejected |
    # confirmed | failed
    status: Mapped[str] = mapped_column(String, default="parsing")

    structured_info: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    clarification_question: Mapped[str | None] = mapped_column(Text, nullable=True)
    clarification_answer: Mapped[str | None] = mapped_column(Text, nullable=True)

    workflow_type: Mapped[str | None] = mapped_column(String, nullable=True)

    moderation_request_result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    moderation_output_result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    rejection_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    output_type: Mapped[str | None] = mapped_column(String, nullable=True)  # image | pdf
    preview_url: Mapped[str | None] = mapped_column(String, nullable=True)
    final_url: Mapped[str | None] = mapped_column(String, nullable=True)

    confirmed: Mapped[bool] = mapped_column(Boolean, default=False)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    # Print-ready PDF (images are resized to the page and converted on confirm)
    print_url: Mapped[str | None] = mapped_column(String, nullable=True)
    page_count: Mapped[int | None] = mapped_column(Integer, nullable=True)


class Material(Base):
    """A worksheet / past paper / notes file uploaded for the mock paper flow."""

    __tablename__ = "materials"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    student_id: Mapped[str] = mapped_column(String, index=True)

    filename: Mapped[str] = mapped_column(String)
    content_type: Mapped[str | None] = mapped_column(String, nullable=True)
    kind: Mapped[str] = mapped_column(String, default="worksheet")  # worksheet | past_paper | notes
    file_url: Mapped[str] = mapped_column(String)
    local_path: Mapped[str | None] = mapped_column(String, nullable=True)

    extracted_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    char_count: Mapped[int] = mapped_column(Integer, default=0)
    page_count: Mapped[int | None] = mapped_column(Integer, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class MockPaper(Base):
    """Mock paper job: materials -> blueprint -> questions + answers -> verify -> render."""

    __tablename__ = "mock_papers"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    student_id: Mapped[str] = mapped_column(String, index=True)

    # queued | blueprinting | writing | verifying | rendering | ready | failed | confirmed
    status: Mapped[str] = mapped_column(String, default="queued")

    title: Mapped[str] = mapped_column(String)
    subject: Mapped[str] = mapped_column(String)
    grade: Mapped[str | None] = mapped_column(String, nullable=True)
    board: Mapped[str | None] = mapped_column(String, nullable=True)
    total_marks: Mapped[int] = mapped_column(Integer, default=50)
    duration_minutes: Mapped[int] = mapped_column(Integer, default=90)
    difficulty: Mapped[str] = mapped_column(String, default="mixed")
    instructions: Mapped[str | None] = mapped_column(Text, nullable=True)
    material_ids: Mapped[list] = mapped_column(JSON, default=list)

    blueprint: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    questions: Mapped[list | None] = mapped_column(JSON, nullable=True)
    verification: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    source: Mapped[str | None] = mapped_column(String, nullable=True)  # gemini | stub

    paper_url: Mapped[str | None] = mapped_column(String, nullable=True)
    answer_key_url: Mapped[str | None] = mapped_column(String, nullable=True)
    page_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    answer_key_page_count: Mapped[int | None] = mapped_column(Integer, nullable=True)

    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )


class PrintJob(Base):
    """One document waiting for / sent to the printer.

    device: awaiting_confirmation -> sent_to_device
    shared: awaiting_confirmation -> queued -> printing -> completed | failed
    either can be cancelled before it is sent
    """

    __tablename__ = "print_jobs"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    student_id: Mapped[str] = mapped_column(String, index=True)

    source_type: Mapped[str] = mapped_column(String)  # design | paper | answer_key
    # device: student's own printer via the browser print dialog
    # shared: the school's printer via PRINTER_BACKEND (mock | cups | agent)
    destination: Mapped[str] = mapped_column(String, default="device")
    source_id: Mapped[str] = mapped_column(String)
    title: Mapped[str] = mapped_column(String)
    file_url: Mapped[str] = mapped_column(String)

    pages: Mapped[int] = mapped_column(Integer)
    copies: Mapped[int] = mapped_column(Integer, default=1)
    color: Mapped[bool] = mapped_column(Boolean, default=False)
    duplex: Mapped[bool] = mapped_column(Boolean, default=False)
    paper_size: Mapped[str] = mapped_column(String, default="A4")

    status: Mapped[str] = mapped_column(String, default="awaiting_confirmation", index=True)
    printer_backend: Mapped[str | None] = mapped_column(String, nullable=True)
    printer_name: Mapped[str | None] = mapped_column(String, nullable=True)
    external_job_id: Mapped[str | None] = mapped_column(String, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    @property
    def total_pages(self) -> int:
        return self.pages * self.copies
