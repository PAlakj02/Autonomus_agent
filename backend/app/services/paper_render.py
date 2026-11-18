"""Mock paper flow — render stage: verified questions -> HTML -> PDF.

Produces two files: the question paper and the answer key / marking scheme.
Templates live in app/templates and stick to CSS that both WeasyPrint and
xhtml2pdf understand (tables, no flexbox/grid).
"""
import os

from jinja2 import Environment, FileSystemLoader, select_autoescape

from app.services import pdf_utils, storage

_env = Environment(
    loader=FileSystemLoader(os.path.join(os.path.dirname(__file__), "..", "templates")),
    autoescape=select_autoescape(["html"]),
)


def _context(paper, include_answers: bool) -> dict:
    sections = []
    for sec in paper.blueprint["sections"]:
        qs = [q for q in paper.questions if q["section"] == sec["id"]]
        sections.append({**sec, "questions": qs, "marks": sum(q["marks"] for q in qs)})
    return {
        "paper": paper,
        "sections": sections,
        "general_instructions": paper.blueprint.get("general_instructions") or [],
        "include_answers": include_answers,
        "letters": "ABCD",
    }


def render_html(paper, include_answers: bool = False) -> str:
    return _env.get_template("mock_paper.html").render(**_context(paper, include_answers))


def render(paper) -> dict:
    """Returns {"paper_url", "paper_pages", "answer_key_url", "answer_key_pages"}."""
    paper_pdf = pdf_utils.html_to_pdf(render_html(paper, include_answers=False))
    key_pdf = pdf_utils.html_to_pdf(render_html(paper, include_answers=True))
    return {
        "paper_url": storage.save_bytes(paper_pdf, "pdf", subdir="papers"),
        "paper_pages": pdf_utils.count_pages(paper_pdf),
        "answer_key_url": storage.save_bytes(key_pdf, "pdf", subdir="papers"),
        "answer_key_pages": pdf_utils.count_pages(key_pdf),
    }
