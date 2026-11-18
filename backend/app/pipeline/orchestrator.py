"""Ties the four pipeline stages together for a single DesignRequest job:

    Understand -> Generate -> Safety check + Preview -> (awaits) Confirmation

`understand`/`clarify` are handled directly in the router since they're
quick, synchronous, and need to return early for a clarification question.
This module handles stage 2 and 3: generate + moderate + preview.
"""
import logging
from dataclasses import asdict

from sqlalchemy.orm import Session

from app.models import DesignRequest
from app.services import image_gen, content_gen, moderation, pdf_utils, storage

logger = logging.getLogger(__name__)


def run_generation(db: Session, job: DesignRequest) -> None:
    """Runs generate -> moderate output -> preview for a job that is
    ready_to_generate. Mutates `job` and commits status transitions so a
    caller polling GET /requests/{id} can see progress."""

    def moderation_dict(result) -> dict:
        return asdict(result)

    job.status = "moderating_request"
    db.commit()

    summary_text = " ".join(
        str(job.structured_info.get(k, "")) for k in ("subject", "purpose", "style")
    )
    request_mod = moderation.moderate_text(f"{job.raw_request}\n{summary_text}")
    job.moderation_request_result = moderation_dict(request_mod)

    if not request_mod.allowed:
        job.status = "rejected"
        job.rejection_reason = request_mod.reason or "Request failed the safety check."
        db.commit()
        return

    job.status = "generating"
    db.commit()

    workflow_type = job.workflow_type
    try:
        if workflow_type in ("poster_artwork", "logo_icon"):
            asset = image_gen.generate_image(job.structured_info, workflow_type)
            job.output_type = "image"
            job.preview_url = asset["url"]
            moderation_subject = asset["prompt"]

        elif workflow_type in ("flyer_worksheet", "notes_essay"):
            content = content_gen.generate_text_content(job.structured_info, workflow_type)
            pdf_url = content_gen.render_pdf(
                title=job.structured_info.get("subject", "Untitled").title(),
                body_text=content["text"],
                size=job.structured_info.get("size", "A4"),
                orientation=job.structured_info.get("orientation", "portrait"),
            )
            job.output_type = "pdf"
            job.preview_url = pdf_url
            moderation_subject = content["text"]

        else:
            raise ValueError(f"Unknown workflow_type: {workflow_type}")

    except Exception as exc:
        logger.exception("Generation failed for job %s", job.id)
        job.status = "failed"
        job.error_message = str(exc)
        db.commit()
        return

    job.status = "moderating_output"
    db.commit()

    output_mod = moderation.moderate_generated_output(moderation_subject)
    job.moderation_output_result = moderation_dict(output_mod)

    if not output_mod.allowed:
        job.status = "rejected"
        job.rejection_reason = output_mod.reason or "Generated design failed the safety check."
        job.preview_url = None
        db.commit()
        return

    job.status = "preview_ready"
    db.commit()


def make_print_ready(job: DesignRequest) -> None:
    """Design flow "Print-ready file" step: resizes the confirmed image onto
    the requested paper size and converts it to PDF (text designs are already
    PDFs). Sets job.print_url and job.page_count; caller commits."""
    if job.output_type == "image":
        info = job.structured_info or {}
        pdf = pdf_utils.image_to_print_pdf(
            storage.read_bytes(job.final_url),
            size=info.get("size", "A4"),
            orientation=info.get("orientation", "portrait"),
        )
        job.print_url = storage.save_bytes(pdf, "pdf", subdir="print")
        job.page_count = 1
    else:
        job.print_url = job.final_url
        job.page_count = pdf_utils.count_pages(storage.read_bytes(job.final_url))
