"""Thin wrapper around Gemini (google-genai SDK), shared by:

- the mock paper flow's text model (blueprint, questions, answers, audit),
- reading scanned/photographed material (OCR),
- the design flow's image model (see image_gen.py).

PLACEHOLDER: set GEMINI_API_KEY in .env to enable. Until then callers catch
GeminiNotConfigured and fall back to deterministic stubs, so the whole flow
(upload -> generate -> verify -> render -> print) is testable without billing.
"""
import json
import logging

from app.config import settings

logger = logging.getLogger(__name__)

_client = None


class GeminiNotConfigured(Exception):
    pass


def client():
    global _client
    if settings.is_placeholder(settings.gemini_api_key):
        raise GeminiNotConfigured("GEMINI_API_KEY is still a placeholder")
    if _client is None:
        from google import genai

        _client = genai.Client(api_key=settings.gemini_api_key)
    return _client


def is_configured() -> bool:
    return not settings.is_placeholder(settings.gemini_api_key)


def generate_json(system_prompt: str, user_prompt: str, temperature: float = 0.4) -> dict:
    """Runs the text model constrained to a JSON response and parses it."""
    from google.genai import types

    response = client().models.generate_content(
        model=settings.gemini_text_model,
        contents=user_prompt,
        config=types.GenerateContentConfig(
            system_instruction=system_prompt,
            response_mime_type="application/json",
            temperature=temperature,
        ),
    )
    return json.loads(response.text)


def transcribe_image(data: bytes, mime_type: str) -> str:
    """OCR for photographed worksheets / scanned past papers (images or PDF)."""
    from google.genai import types

    response = client().models.generate_content(
        model=settings.gemini_text_model,
        contents=[
            types.Part.from_bytes(data=data, mime_type=mime_type),
            "Transcribe all text in this worksheet or exam paper exactly, keeping "
            "question numbering and marks. Plain text only.",
        ],
    )
    return response.text or ""


def generate_image(prompt: str, aspect_ratio: str) -> bytes:
    """Returns PNG/JPEG bytes from the Gemini image model."""
    from google.genai import types

    response = client().models.generate_content(
        model=settings.gemini_image_model,
        contents=prompt,
        config=types.GenerateContentConfig(
            response_modalities=["TEXT", "IMAGE"],
            image_config=types.ImageConfig(aspect_ratio=aspect_ratio),
        ),
    )
    for part in response.candidates[0].content.parts or []:
        if part.inline_data is not None and part.inline_data.data:
            return part.inline_data.data
    raise RuntimeError(f"Gemini returned no image (finish reason: {response.candidates[0].finish_reason})")
