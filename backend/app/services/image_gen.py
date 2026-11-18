"""Stage 2 (image workflows): posters/artwork and logos/icons.

PLACEHOLDER: set GEMINI_API_KEY in .env to generate real images with the
Gemini image model (GEMINI_IMAGE_MODEL). Until then (or on any API error), falls back to a locally
rendered placeholder image via Pillow, so the full pipeline (moderation,
preview, confirmation) can be developed and tested without billing.
"""
import logging

from PIL import Image, ImageDraw, ImageFont

from app.services import gemini_text, storage

logger = logging.getLogger(__name__)

# size in points at 150dpi-ish resolution, close enough for previews
_SIZE_PX = {
    "A3": (1587, 2245),
    "A4": (1123, 1587),
    "A5": (794, 1123),
    "LETTER": (1275, 1650),
}


class ImageGenNotConfigured(Exception):
    pass


def _dimensions(size: str, orientation: str) -> tuple[int, int]:
    w, h = _SIZE_PX.get((size or "A4").upper(), _SIZE_PX["A4"])
    if (orientation or "portrait").lower() == "landscape":
        w, h = h, w
    return w, h


def build_prompt(structured_info: dict, workflow_type: str) -> str:
    subject = structured_info.get("subject", "the requested topic")
    style = structured_info.get("style", "clean and colorful")
    if workflow_type == "logo_icon":
        return (
            f"A simple, flat, vector-style logo/icon representing '{subject}', "
            f"{style} style, centered on a plain background, no text unless requested."
        )
    return (
        f"An educational {structured_info.get('size', 'A4')} "
        f"{structured_info.get('orientation', 'portrait')} poster about '{subject}', "
        f"{style} style, suitable for a student audience, clear labels and legible layout."
    )


def _call_gemini(prompt: str, structured_info: dict) -> bytes:
    try:
        portrait = (structured_info.get("orientation") or "portrait").lower() != "landscape"
        # Paper is ~1:1.41; 3:4 / 4:3 are the closest ratios the model offers.
        return gemini_text.generate_image(prompt, "3:4" if portrait else "4:3")
    except gemini_text.GeminiNotConfigured as exc:
        raise ImageGenNotConfigured(str(exc)) from exc


def _placeholder_image(prompt: str, structured_info: dict, workflow_type: str) -> bytes:
    w, h = _dimensions(structured_info.get("size", "A4"), structured_info.get("orientation", "portrait"))
    img = Image.new("RGB", (w, h), color=(238, 245, 255))
    draw = ImageDraw.Draw(img)

    margin = w // 20
    draw.rectangle([margin, margin, w - margin, h - margin], outline=(70, 110, 200), width=6)

    title = structured_info.get("subject", "Untitled").title() or "Untitled"
    subtitle = f"[placeholder image — {workflow_type}]"
    style_line = f"style: {structured_info.get('style', 'clean and colorful')}"

    try:
        title_font = ImageFont.truetype("Arial.ttf", size=w // 18)
        body_font = ImageFont.truetype("Arial.ttf", size=w // 40)
    except Exception:
        title_font = ImageFont.load_default()
        body_font = ImageFont.load_default()

    draw.multiline_text(
        (w / 2, h / 2 - h / 10),
        title,
        fill=(30, 40, 80),
        font=title_font,
        anchor="mm",
        align="center",
    )
    draw.multiline_text(
        (w / 2, h / 2 + h / 12),
        f"{subtitle}\n{style_line}",
        fill=(90, 100, 130),
        font=body_font,
        anchor="mm",
        align="center",
    )

    from io import BytesIO

    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def generate_image(structured_info: dict, workflow_type: str) -> dict:
    """Returns {"url": str, "prompt": str, "source": "gemini"|"placeholder"}."""
    prompt = build_prompt(structured_info, workflow_type)

    try:
        data = _call_gemini(prompt, structured_info)
        source = "gemini"
    except ImageGenNotConfigured:
        data = _placeholder_image(prompt, structured_info, workflow_type)
        source = "placeholder"
    except Exception:
        logger.exception("Gemini image generation failed, using placeholder image")
        data = _placeholder_image(prompt, structured_info, workflow_type)
        source = "placeholder"

    url = storage.save_bytes(data, "png", subdir="images")
    return {"url": url, "prompt": prompt, "source": source}
