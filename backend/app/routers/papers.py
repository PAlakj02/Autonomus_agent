"""Mock paper flow API: upload material, generate, poll, regenerate."""
import os

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.dependencies import get_current_student
from app.models import Material, MockPaper
from app.pipeline import paper_pipeline
from app.schemas import CreatePaperIn, MaterialOut, MockPaperOut
from app.services import material_extract, storage

router = APIRouter(prefix="/api", tags=["mock papers"])

MATERIAL_KINDS = ("worksheet", "past_paper", "notes")


# ---- Materials ----


@router.post("/materials", response_model=MaterialOut)
async def upload_material(
    file: UploadFile = File(...),
    kind: str = Form("worksheet"),
    db: Session = Depends(get_db),
    student_id: str = Depends(get_current_student),
):
    """Upload material stage: stores a worksheet/past paper/notes file and
    extracts its text for the question writer."""
    if kind not in MATERIAL_KINDS:
        raise HTTPException(400, f"kind must be one of {MATERIAL_KINDS}")
    content_type = material_extract.resolve_content_type(file.filename or "", file.content_type)
    if content_type is None:
        raise HTTPException(415, "Upload a PDF, DOCX, TXT/MD, or an image (PNG/JPG/WEBP).")

    data = await file.read()
    if len(data) > settings.upload_max_mb * 1024 * 1024:
        raise HTTPException(413, f"File is larger than {settings.upload_max_mb} MB")
    if not data:
        raise HTTPException(400, "File is empty")

    try:
        text, pages = material_extract.extract(data, content_type)
    except Exception as exc:
        raise HTTPException(422, f"Could not read this file: {exc}")

    ext = material_extract.ALLOWED_TYPES[content_type]
    url = storage.save_bytes(data, ext, subdir="materials")
    material = Material(
        student_id=student_id,
        filename=os.path.basename(file.filename or f"upload.{ext}")[:200],
        content_type=content_type,
        kind=kind,
        file_url=url,
        extracted_text=text,
        char_count=len(text),
        page_count=pages,
    )
    db.add(material)
    db.commit()
    db.refresh(material)
    return material


@router.get("/materials", response_model=list[MaterialOut])
def list_materials(db: Session = Depends(get_db), student_id: str = Depends(get_current_student)):
    return (
        db.query(Material)
        .filter(Material.student_id == student_id)
        .order_by(Material.created_at.desc())
        .all()
    )


@router.delete("/materials/{material_id}", status_code=204)
def delete_material(
    material_id: str, db: Session = Depends(get_db), student_id: str = Depends(get_current_student)
):
    m = db.get(Material, material_id)
    if m is None or m.student_id != student_id:
        raise HTTPException(404, "Material not found")
    db.delete(m)
    db.commit()


# ---- Papers ----


def _get_paper(db: Session, paper_id: str, student_id: str) -> MockPaper:
    paper = db.get(MockPaper, paper_id)
    if paper is None or paper.student_id != student_id:
        raise HTTPException(404, "Paper not found")
    return paper


@router.post("/papers", response_model=MockPaperOut, status_code=202)
def create_paper(
    body: CreatePaperIn,
    background: BackgroundTasks,
    db: Session = Depends(get_db),
    student_id: str = Depends(get_current_student),
):
    """Starts generation in the background. Poll GET /api/papers/{id}; status
    moves queued -> blueprinting -> writing -> verifying -> rendering -> ready."""
    if body.material_ids:
        owned = (
            db.query(Material.id)
            .filter(Material.id.in_(body.material_ids), Material.student_id == student_id)
            .count()
        )
        if owned != len(set(body.material_ids)):
            raise HTTPException(404, "One or more materials not found")

    paper = MockPaper(
        student_id=student_id,
        title=body.title or f"{body.subject.strip().title()} Mock Paper",
        **body.model_dump(exclude={"title"}),
    )
    db.add(paper)
    db.commit()
    db.refresh(paper)
    background.add_task(paper_pipeline.run_paper, paper.id)
    return paper


@router.get("/papers", response_model=list[MockPaperOut])
def list_papers(db: Session = Depends(get_db), student_id: str = Depends(get_current_student)):
    return (
        db.query(MockPaper)
        .filter(MockPaper.student_id == student_id)
        .order_by(MockPaper.created_at.desc())
        .all()
    )


@router.get("/papers/{paper_id}", response_model=MockPaperOut)
def get_paper(paper_id: str, db: Session = Depends(get_db), student_id: str = Depends(get_current_student)):
    return _get_paper(db, paper_id, student_id)


@router.post("/papers/{paper_id}/regenerate", response_model=MockPaperOut, status_code=202)
def regenerate_paper(
    paper_id: str,
    background: BackgroundTasks,
    db: Session = Depends(get_db),
    student_id: str = Depends(get_current_student),
):
    paper = _get_paper(db, paper_id, student_id)
    if paper.status not in ("ready", "failed"):
        raise HTTPException(400, f"Paper is still being generated (status={paper.status})")
    for field in ("blueprint", "questions", "verification", "paper_url", "answer_key_url",
                  "page_count", "answer_key_page_count", "error_message"):
        setattr(paper, field, None)
    paper.status = "queued"
    db.commit()
    db.refresh(paper)
    background.add_task(paper_pipeline.run_paper, paper.id)
    return paper
