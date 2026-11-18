"""PDF helpers shared by the design flow, mock paper flow and print queue.

- count_pages: page limits in the print queue are enforced from the real file.
- image_to_print_pdf: "Print-ready file" step of the design flow — fits the
  generated image onto an exact paper size at 300dpi and wraps it in a PDF.
- html_to_pdf: "Verify and render" step of the mock paper flow. Uses
  WeasyPrint when its system libraries (pango) are installed, otherwise the
  pure-Python xhtml2pdf renderer.
"""
import logging
from io import BytesIO

from PIL import Image

logger = logging.getLogger(__name__)

# Paper sizes in millimetres (portrait)
PAPER_MM = {
    "A3": (297, 420),
    "A4": (210, 297),
    "A5": (148, 210),
    "LETTER": (216, 279),
}
PRINT_DPI = 300


def count_pages(pdf_bytes: bytes) -> int:
    from pypdf import PdfReader

    return len(PdfReader(BytesIO(pdf_bytes)).pages)


def image_to_print_pdf(image_bytes: bytes, size: str = "A4", orientation: str = "portrait") -> bytes:
    """Scales the image to fit the page (keeping aspect ratio), centres it on a
    white page with a 5mm safe margin, and returns a single-page 300dpi PDF."""
    w_mm, h_mm = PAPER_MM.get((size or "A4").upper(), PAPER_MM["A4"])
    if (orientation or "portrait").lower() == "landscape":
        w_mm, h_mm = h_mm, w_mm

    px = lambda mm: round(mm / 25.4 * PRINT_DPI)  # noqa: E731
    page_w, page_h = px(w_mm), px(h_mm)
    margin = px(5)

    img = Image.open(BytesIO(image_bytes)).convert("RGB")
    box_w, box_h = page_w - 2 * margin, page_h - 2 * margin
    scale = min(box_w / img.width, box_h / img.height)
    img = img.resize((max(1, round(img.width * scale)), max(1, round(img.height * scale))), Image.LANCZOS)

    page = Image.new("RGB", (page_w, page_h), "white")
    page.paste(img, ((page_w - img.width) // 2, (page_h - img.height) // 2))

    buf = BytesIO()
    page.save(buf, format="PDF", resolution=PRINT_DPI)
    return buf.getvalue()


def html_to_pdf(html: str) -> bytes:
    try:
        from weasyprint import HTML  # needs pango/cairo system libs

        return HTML(string=html).write_pdf()
    except Exception as exc:  # ImportError or missing native libs (OSError)
        logger.debug("WeasyPrint unavailable (%s); using xhtml2pdf", exc)

    from xhtml2pdf import pisa

    buf = BytesIO()
    result = pisa.CreatePDF(html, dest=buf, encoding="utf-8")
    if result.err:
        raise RuntimeError(f"HTML to PDF rendering failed with {result.err} error(s)")
    return buf.getvalue()
