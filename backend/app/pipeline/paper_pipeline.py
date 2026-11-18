"""Mock paper flow orchestrator:

    Upload material -> Gemini text model (blueprint, questions, answers)
                    -> Verify (marks check) and render (HTML -> PDF)

Runs as a FastAPI background task with its own DB session. Each stage commits
its status (blueprinting -> writing -> verifying -> rendering -> ready) so the
frontend can poll GET /api/papers/{id} and show progress.
"""
import logging

from app.database import SessionLocal
from app.models import Material, MockPaper
from app.services import moderation, paper_gen, paper_render

logger = logging.getLogger(__name__)


def _spec(db, paper: MockPaper) -> paper_gen.PaperSpec:
    materials = []
    if paper.material_ids:
        rows = (
            db.query(Material)
            .filter(Material.id.in_(paper.material_ids), Material.student_id == paper.student_id)
            .all()
        )
        materials = [{"filename": m.filename, "kind": m.kind, "text": m.extracted_text or ""} for m in rows]
    return paper_gen.PaperSpec(
        subject=paper.subject,
        total_marks=paper.total_marks,
        duration_minutes=paper.duration_minutes,
        difficulty=paper.difficulty,
        grade=paper.grade,
        board=paper.board,
        instructions=paper.instructions,
        materials=materials,
    )


def run_paper(paper_id: str) -> None:
    db = SessionLocal()
    try:
        paper = db.get(MockPaper, paper_id)
        if paper is None:
            return

        def stage(status: str) -> None:
            paper.status = status
            db.commit()

        try:
            request_mod = moderation.moderate_text(f"{paper.subject}\n{paper.instructions or ''}")
            if not request_mod.allowed:
                paper.status = "failed"
                paper.error_message = request_mod.reason
                db.commit()
                return

            spec = _spec(db, paper)

            stage("blueprinting")
            blueprint, source, bp_fixes = paper_gen.build_blueprint(spec)
            paper.blueprint = blueprint
            paper.source = source

            stage("writing")
            questions = paper_gen.write_questions(spec, blueprint, source)

            stage("verifying")
            report = paper_gen.verify_and_repair(spec, blueprint, questions, source)
            report["auto_fixes"] = bp_fixes + report["auto_fixes"]
            output_mod = moderation.moderate_generated_output(
                "\n".join(f"{q.get('question')} {q.get('answer')}" for q in questions)
            )
            report["moderation"] = {"allowed": output_mod.allowed, "flagged_terms": output_mod.flagged_terms}
            paper.questions = questions
            paper.verification = report
            db.commit()

            if not report["passed"]:
                paper.status = "failed"
                paper.error_message = "Paper failed verification: " + "; ".join(report["issues"][:5])
                db.commit()
                return
            if not output_mod.allowed:
                paper.status = "failed"
                paper.error_message = output_mod.reason or "Generated paper failed the safety check."
                db.commit()
                return

            stage("rendering")
            files = paper_render.render(paper)
            paper.paper_url = files["paper_url"]
            paper.page_count = files["paper_pages"]
            paper.answer_key_url = files["answer_key_url"]
            paper.answer_key_page_count = files["answer_key_pages"]
            stage("ready")

        except Exception as exc:
            logger.exception("Mock paper %s failed", paper_id)
            db.rollback()
            paper = db.get(MockPaper, paper_id)
            paper.status = "failed"
            paper.error_message = str(exc)
            db.commit()
    finally:
        db.close()
