from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import get_current_student
from app.models import DesignRequest
from app.pipeline import orchestrator
from app.schemas import ClarifyIn, CreateRequestIn, DesignRequestOut
from app.services import parser

router = APIRouter(prefix="/api/requests", tags=["requests"])


def _apply_structured_info(job: DesignRequest, structured_info: dict) -> None:
    job.structured_info = structured_info
    job.workflow_type = structured_info.get("workflow_type")
    if parser.needs_clarification(structured_info):
        job.status = "pending_clarification"
        job.clarification_question = structured_info["clarification_question"]
    else:
        job.status = "ready_to_generate"
        job.clarification_question = None


@router.post("", response_model=DesignRequestOut)
def create_request(
    body: CreateRequestIn,
    db: Session = Depends(get_db),
    student_id: str = Depends(get_current_student),
):
    """Stage 1: Understand. Creates a job and parses the free-text request."""
    job = DesignRequest(raw_request=body.raw_request, student_id=student_id, status="parsing")
    structured_info = parser.understand_request(body.raw_request)
    _apply_structured_info(job, structured_info)

    db.add(job)
    db.commit()
    db.refresh(job)
    return job


@router.get("", response_model=list[DesignRequestOut])
def list_requests(
    db: Session = Depends(get_db),
    student_id: str = Depends(get_current_student),
):
    return (
        db.query(DesignRequest)
        .filter(DesignRequest.student_id == student_id)
        .order_by(DesignRequest.created_at.desc())
        .all()
    )


def _get_job_or_404(db: Session, job_id: str, student_id: str) -> DesignRequest:
    job = db.query(DesignRequest).filter(DesignRequest.id == job_id).first()
    if job is None:
        raise HTTPException(status_code=404, detail="Request not found")
    if job.student_id != student_id:
        raise HTTPException(status_code=403, detail="Not your request")
    return job


@router.get("/{job_id}", response_model=DesignRequestOut)
def get_request(
    job_id: str,
    db: Session = Depends(get_db),
    student_id: str = Depends(get_current_student),
):
    return _get_job_or_404(db, job_id, student_id)


@router.post("/{job_id}/clarify", response_model=DesignRequestOut)
def clarify_request(
    job_id: str,
    body: ClarifyIn,
    db: Session = Depends(get_db),
    student_id: str = Depends(get_current_student),
):
    """Answers the one clarification question from stage 1, if any."""
    job = _get_job_or_404(db, job_id, student_id)
    if job.status != "pending_clarification":
        raise HTTPException(status_code=400, detail=f"Job is not awaiting clarification (status={job.status})")

    job.clarification_answer = body.answer
    structured_info = parser.understand_request(body.answer, previous=job.structured_info)
    _apply_structured_info(job, structured_info)

    db.commit()
    db.refresh(job)
    return job


@router.post("/{job_id}/generate", response_model=DesignRequestOut)
def generate_request(
    job_id: str,
    db: Session = Depends(get_db),
    student_id: str = Depends(get_current_student),
):
    """Stages 2 + 3: Generate the design, run safety checks, produce a preview."""
    job = _get_job_or_404(db, job_id, student_id)
    if job.status != "ready_to_generate":
        raise HTTPException(
            status_code=400,
            detail=f"Job is not ready to generate (status={job.status})",
        )

    orchestrator.run_generation(db, job)
    db.refresh(job)
    return job


@router.post("/{job_id}/confirm", response_model=DesignRequestOut)
def confirm_request(
    job_id: str,
    db: Session = Depends(get_db),
    student_id: str = Depends(get_current_student),
):
    """Stage 4: Student confirmation. Locks in the previewed design as final
    and produces the print-ready PDF used by the print queue."""
    job = _get_job_or_404(db, job_id, student_id)
    if job.status != "preview_ready":
        raise HTTPException(
            status_code=400,
            detail=f"Job has no preview to confirm (status={job.status})",
        )

    job.confirmed = True
    job.status = "confirmed"
    job.final_url = job.preview_url
    orchestrator.make_print_ready(job)
    db.commit()
    db.refresh(job)
    return job


@router.post("/{job_id}/reject", response_model=DesignRequestOut)
def reject_request(
    job_id: str,
    db: Session = Depends(get_db),
    student_id: str = Depends(get_current_student),
):
    """Student rejects the preview (e.g. wants a different design). Sends the
    job back to ready_to_generate so /generate can be called again."""
    job = _get_job_or_404(db, job_id, student_id)
    if job.status != "preview_ready":
        raise HTTPException(
            status_code=400,
            detail=f"Job has no preview to reject (status={job.status})",
        )

    job.status = "ready_to_generate"
    job.preview_url = None
    db.commit()
    db.refresh(job)
    return job
