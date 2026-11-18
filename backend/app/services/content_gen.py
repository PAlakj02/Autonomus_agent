"""Stage 2 (text workflows): flyers/worksheets (content + layout) and notes/essays.

Uses Groq for text generation when configured (see groq_client.py), else a
deterministic stub, then renders the content to a simple PDF with reportlab.
"""
import logging
from io import BytesIO

from reportlab.lib.pagesizes import A4, A5, A3, LETTER, landscape, portrait
from reportlab.lib.units import cm
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet

from app.services import groq_client, storage

logger = logging.getLogger(__name__)

_PAGE_SIZES = {"A3": A3, "A4": A4, "A5": A5, "LETTER": LETTER}

CONTENT_SYSTEM_PROMPT = """You are a content writer for a student design tool.
Write clear, age-appropriate educational content for the requested subject and
purpose. Return plain text organized into short sections with headings on
their own line followed by a colon, no markdown symbols."""


def _stub_content(subject: str, purpose: str, workflow_type: str) -> str:
    subject = subject or "the topic"
    if workflow_type == "notes_essay":
        return (
            f"{subject.title()}\n\n"
            f"Introduction:\nThis document provides an overview of {subject}, "
            "covering its key concepts and significance.\n\n"
            f"Key Points:\n1. What {subject} is and why it matters.\n"
            f"2. Core concepts related to {subject}.\n"
            f"3. Real-world examples involving {subject}.\n\n"
            "Conclusion:\nA brief summary tying the key points together.\n\n"
            "[placeholder content — connect GROQ_API_KEY for AI-generated text]"
        )
    return (
        f"{subject.title()}\n\n"
        f"Overview:\nKey facts and highlights about {subject}, presented for "
        f"a {purpose or 'student handout'}.\n\n"
        "Section 1:\nMain idea one with a short explanation.\n\n"
        "Section 2:\nMain idea two with a short explanation.\n\n"
        "Activity:\nA short question or task for the student to complete.\n\n"
        "[placeholder content — connect GROQ_API_KEY for AI-generated text]"
    )


def generate_text_content(structured_info: dict, workflow_type: str) -> dict:
    subject = structured_info.get("subject", "")
    purpose = structured_info.get("purpose", "")

    user_prompt = (
        f"Subject: {subject}\nPurpose: {purpose}\nWorkflow: {workflow_type}\n"
        f"Style: {structured_info.get('style', 'clean and colorful')}"
    )

    try:
        text = groq_client.chat_text(CONTENT_SYSTEM_PROMPT, user_prompt)
        source = "groq"
    except groq_client.GroqNotConfigured:
        text = _stub_content(subject, purpose, workflow_type)
        source = "stub"
    except Exception:
        logger.exception("Groq content generation failed, using stub content")
        text = _stub_content(subject, purpose, workflow_type)
        source = "stub"

    return {"text": text, "source": source}


def render_pdf(title: str, body_text: str, size: str = "A4", orientation: str = "portrait") -> str:
    page_size = _PAGE_SIZES.get((size or "A4").upper(), A4)
    page_size = landscape(page_size) if (orientation or "portrait").lower() == "landscape" else portrait(page_size)

    buf = BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=page_size,
        topMargin=2 * cm, bottomMargin=2 * cm, leftMargin=2 * cm, rightMargin=2 * cm,
    )
    styles = getSampleStyleSheet()
    story = [Paragraph(title or "Untitled", styles["Title"]), Spacer(1, 0.5 * cm)]

    for block in body_text.split("\n\n"):
        block = block.strip()
        if not block:
            continue
        lines = block.split("\n", 1)
        if lines[0].endswith(":"):
            story.append(Paragraph(lines[0], styles["Heading2"]))
            if len(lines) > 1:
                story.append(Paragraph(lines[1].replace("\n", "<br/>"), styles["BodyText"]))
        else:
            story.append(Paragraph(block.replace("\n", "<br/>"), styles["BodyText"]))
        story.append(Spacer(1, 0.3 * cm))

    doc.build(story)
    return storage.save_bytes(buf.getvalue(), "pdf", subdir="documents")
