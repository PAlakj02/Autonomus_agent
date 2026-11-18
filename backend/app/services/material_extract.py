"""Upload material stage: pulls plain text out of worksheets / past papers so
the text model can use them as source material.

Supports PDF (text layer), DOCX, TXT/MD, and images (via Gemini OCR when
configured; otherwise stored with no text and the student is told).
"""
import logging
from io import BytesIO

from app.services import gemini_text

logger = logging.getLogger(__name__)

ALLOWED_TYPES = {
    "application/pdf": "pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
    "text/plain": "txt",
    "text/markdown": "md",
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/webp": "webp",
}
EXT_TO_TYPE = {
    "pdf": "application/pdf",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "txt": "text/plain",
    "md": "text/markdown",
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "webp": "image/webp",
}

MAX_CHARS = 60_000  # per file, keeps prompts within budget


def resolve_content_type(filename: str, declared: str | None) -> str | None:
    if declared in ALLOWED_TYPES:
        return declared
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    return EXT_TO_TYPE.get(ext)


def extract(data: bytes, content_type: str) -> tuple[str, int | None]:
    """Returns (text, page_count)."""
    kind = ALLOWED_TYPES[content_type]

    if kind == "pdf":
        from pypdf import PdfReader

        reader = PdfReader(BytesIO(data))
        text = "\n\n".join((page.extract_text() or "") for page in reader.pages)
        if not text.strip() and gemini_text.is_configured():
            # Scanned PDF with no text layer: let Gemini read it directly.
            try:
                text = gemini_text.transcribe_image(data, "application/pdf")
            except Exception:
                logger.exception("Gemini OCR of scanned PDF failed")
        return text[:MAX_CHARS], len(reader.pages)

    if kind == "docx":
        import docx

        document = docx.Document(BytesIO(data))
        text = "\n".join(p.text for p in document.paragraphs)
        return text[:MAX_CHARS], None

    if kind in ("txt", "md"):
        return data.decode("utf-8", errors="replace")[:MAX_CHARS], None

    # images
    try:
        return gemini_text.transcribe_image(data, content_type)[:MAX_CHARS], 1
    except gemini_text.GeminiNotConfigured:
        return "", 1
    except Exception:
        logger.exception("Gemini OCR failed")
        return "", 1
