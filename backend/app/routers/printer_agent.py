"""Endpoints for the remote print agent (PRINTER_BACKEND=agent).

The agent authenticates with `X-Agent-Token: <PRINT_AGENT_TOKEN>`, claims the
oldest queued job, downloads its PDF, prints locally and reports the result.
"""
import secrets
import time
from datetime import datetime

from fastapi import APIRouter, Depends, Header, HTTPException, Response
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models import PrintJob
from app.printing import backends
from app.schemas import AgentJobUpdateIn
from app.services import storage

router = APIRouter(prefix="/api/printer-agent", tags=["print agent"])


def require_agent(x_agent_token: str | None = Header(default=None)) -> None:
    if settings.printer_backend != "agent":
        raise HTTPException(409, "PRINTER_BACKEND is not 'agent'")
    if settings.is_placeholder(settings.print_agent_token):
        raise HTTPException(503, "PRINT_AGENT_TOKEN is not configured on the server")
    if not x_agent_token or not secrets.compare_digest(x_agent_token, settings.print_agent_token):
        raise HTTPException(401, "Bad agent token")
    backends.agent_last_seen = time.time()


@router.post("/claim", dependencies=[Depends(require_agent)])
def claim_next_job(printer_name: str = "default", db: Session = Depends(get_db)):
    """Returns the next job to print (204 if the queue is empty)."""
    job = (
        db.query(PrintJob)
        .filter(PrintJob.status == "queued")
        .order_by(PrintJob.confirmed_at.asc())
        .first()
    )
    if job is None:
        return Response(status_code=204)
    job.status = "printing"
    job.printer_backend = "agent"
    job.printer_name = printer_name
    db.commit()
    return {
        "id": job.id,
        "title": job.title,
        "copies": job.copies,
        "color": job.color,
        "duplex": job.duplex,
        "paper_size": job.paper_size,
        "pages": job.pages,
        "download_path": f"/api/printer-agent/jobs/{job.id}/file",
    }


@router.get("/jobs/{job_id}/file", dependencies=[Depends(require_agent)])
def download_job_file(job_id: str, db: Session = Depends(get_db)):
    job = db.get(PrintJob, job_id)
    if job is None:
        raise HTTPException(404, "Job not found")
    return Response(storage.read_bytes(job.file_url), media_type="application/pdf")


@router.post("/jobs/{job_id}/status", dependencies=[Depends(require_agent)])
def update_job_status(job_id: str, body: AgentJobUpdateIn, db: Session = Depends(get_db)):
    job = db.get(PrintJob, job_id)
    if job is None:
        raise HTTPException(404, "Job not found")
    job.status = body.status
    job.error_message = body.error_message
    if body.external_job_id:
        job.external_job_id = body.external_job_id
    if body.status == "completed":
        job.completed_at = datetime.utcnow()
    db.commit()
    return {"ok": True}


@router.post("/heartbeat", dependencies=[Depends(require_agent)])
def heartbeat():
    return {"ok": True}
