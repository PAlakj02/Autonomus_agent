"""Print queue API: create (preview page count) -> confirm -> printer."""
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.dependencies import get_current_student
from app.models import DesignRequest, MockPaper, PrintJob
from app.pipeline import orchestrator
from app.printing import backends, quota
from app.schemas import CreatePrintJobIn, PrinterStatusOut, PrintJobOut, PrintQuotaOut

router = APIRouter(prefix="/api", tags=["print queue"])


def _resolve_source(db: Session, body: CreatePrintJobIn, student_id: str) -> tuple[str, str, int, str]:
    """Returns (title, file_url, pages, paper_size) for the thing being printed."""
    if body.source_type == "design":
        job = db.get(DesignRequest, body.source_id)
        if job is None or job.student_id != student_id:
            raise HTTPException(404, "Design not found")
        if job.status != "confirmed":
            raise HTTPException(400, "Confirm the design before printing it")
        if not job.print_url:
            orchestrator.make_print_ready(job)
            db.commit()
        info = job.structured_info or {}
        title = f"{(info.get('subject') or 'Design').title()} {info.get('purpose') or ''}".strip()
        return title, job.print_url, job.page_count or 1, (info.get("size") or "A4").upper()

    paper = db.get(MockPaper, body.source_id)
    if paper is None or paper.student_id != student_id:
        raise HTTPException(404, "Paper not found")
    if paper.status not in ("ready", "confirmed"):
        raise HTTPException(400, f"Paper is not ready to print (status={paper.status})")
    if body.source_type == "answer_key":
        return f"{paper.title} — Answer Key", paper.answer_key_url, paper.answer_key_page_count or 1, "A4"
    return paper.title, paper.paper_url, paper.page_count or 1, "A4"


def _get_job(db: Session, job_id: str, student_id: str) -> PrintJob:
    job = db.get(PrintJob, job_id)
    if job is None or job.student_id != student_id:
        raise HTTPException(404, "Print job not found")
    return job


@router.post("/print-jobs", response_model=PrintJobOut)
def create_print_job(
    body: CreatePrintJobIn, db: Session = Depends(get_db), student_id: str = Depends(get_current_student)
):
    """Adds a document to the queue as `awaiting_confirmation`. Nothing prints
    until the student confirms with the page count in front of them."""
    if body.destination == "shared" and settings.printer_backend == "none":
        raise HTTPException(400, "No shared printer is set up — print to your own printer instead")
    if body.copies > settings.print_max_copies:
        raise HTTPException(400, f"At most {settings.print_max_copies} copies per job")
    title, file_url, pages, paper_size = _resolve_source(db, body, student_id)
    if pages * body.copies > settings.print_max_pages_per_job:
        raise HTTPException(
            400,
            f"This job is {pages * body.copies} pages; the limit per job is "
            f"{settings.print_max_pages_per_job}. Reduce copies.",
        )
    job = PrintJob(
        student_id=student_id,
        source_type=body.source_type,
        source_id=body.source_id,
        destination=body.destination,
        title=title,
        file_url=file_url,
        pages=pages,
        copies=body.copies,
        color=body.color,
        duplex=body.duplex,
        paper_size=paper_size,
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


@router.post("/print-jobs/{job_id}/confirm", response_model=PrintJobOut)
def confirm_print_job(job_id: str, db: Session = Depends(get_db), student_id: str = Depends(get_current_student)):
    job = _get_job(db, job_id, student_id)
    if job.status != "awaiting_confirmation":
        raise HTTPException(400, f"Job is not awaiting confirmation (status={job.status})")
    remaining = quota.usage(db, student_id)["remaining"]
    if job.total_pages > remaining:
        raise HTTPException(
            403,
            f"Daily page limit reached: this job needs {job.total_pages} pages, "
            f"you have {remaining} left today.",
        )
    job.confirmed_at = datetime.utcnow()
    if job.destination == "device":
        # The frontend now opens the browser print dialog with this PDF.
        job.status = "sent_to_device"
        job.completed_at = job.confirmed_at
    else:
        job.status = "queued"
    db.commit()
    db.refresh(job)
    return job


@router.post("/print-jobs/{job_id}/cancel", response_model=PrintJobOut)
def cancel_print_job(job_id: str, db: Session = Depends(get_db), student_id: str = Depends(get_current_student)):
    job = _get_job(db, job_id, student_id)
    if job.status not in ("awaiting_confirmation", "queued"):
        raise HTTPException(400, f"Job can no longer be cancelled (status={job.status})")
    job.status = "cancelled"
    db.commit()
    db.refresh(job)
    return job


@router.get("/print-jobs", response_model=list[PrintJobOut])
def list_print_jobs(db: Session = Depends(get_db), student_id: str = Depends(get_current_student)):
    return (
        db.query(PrintJob)
        .filter(PrintJob.student_id == student_id)
        .order_by(PrintJob.created_at.desc())
        .limit(50)
        .all()
    )


@router.get("/print-quota", response_model=PrintQuotaOut)
def get_quota(db: Session = Depends(get_db), student_id: str = Depends(get_current_student)):
    return quota.usage(db, student_id)


@router.get("/printer", response_model=PrinterStatusOut)
def printer_status(db: Session = Depends(get_db)):
    online, detail = backends.status()
    return {
        "shared_available": settings.printer_backend != "none",
        "backend": settings.printer_backend,
        "printer_name": settings.printer_name or None,
        "online": online,
        "detail": detail,
        "queued_jobs": db.query(PrintJob).filter(PrintJob.status == "queued").count(),
    }
