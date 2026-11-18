"""Stage 1: Understand the request.

Converts a student's free-text request into structured information and picks
a workflow_type. Uses Groq/Llama when configured; otherwise falls back to a
deterministic rule-based stub so the pipeline works out of the box.
"""
import json
import logging
import re

from app.services import groq_client

logger = logging.getLogger(__name__)

WORKFLOW_TYPES = ["poster_artwork", "flyer_worksheet", "logo_icon", "notes_essay"]

REQUIRED_FIELDS = ["subject", "purpose", "size", "orientation", "style"]

SYSTEM_PROMPT = f"""You are the request-understanding stage of a student design tool.
Convert the student's natural-language request into strict JSON with these keys:
- subject (string): what the design is about
- purpose (string): e.g. poster, flyer, worksheet, logo, icon, notes, essay
- size (string): paper size, default "A4" if not specified
- orientation (string): "portrait" or "landscape", default "portrait"
- style (string): visual style, default "clean and colorful"
- workflow_type (string): one of {WORKFLOW_TYPES}
- missing_fields (array of strings): subset of {REQUIRED_FIELDS} that the student
  did not specify and were only filled in with defaults you guessed
- clarification_question (string or null): ONE short question to ask the student
  if a critical field (subject or purpose) is missing/ambiguous, else null

Respond with ONLY the JSON object, no commentary.
"""


def _select_workflow_type(purpose: str) -> str:
    p = purpose.lower()
    if any(k in p for k in ["logo", "icon", "emblem", "badge"]):
        return "logo_icon"
    if any(k in p for k in ["flyer", "worksheet", "handout", "pamphlet"]):
        return "flyer_worksheet"
    if any(k in p for k in ["note", "essay", "report", "summary", "write-up", "writeup"]):
        return "notes_essay"
    return "poster_artwork"


def _stub_parse(raw_text: str, previous: dict | None) -> dict:
    """Deterministic fallback parser used when Groq isn't configured."""
    text = raw_text.strip()
    lower = text.lower()

    size = "A4"
    size_match = re.search(r"\b(a3|a4|a5|letter)\b", lower)
    if size_match:
        size = size_match.group(1).upper()

    orientation = "landscape" if "landscape" in lower else "portrait"

    style_match = re.search(
        r"\b(colorful|colourful|minimal(?:ist)?|clean|playful|professional|scientific|"
        r"cartoon|realistic|watercolor|hand[- ]drawn)\b",
        lower,
    )
    style = style_match.group(1) if style_match else "clean and colorful"

    purpose_match = re.search(
        r"\b(poster|flyer|worksheet|logo|icon|notes?|essay|report)\b", lower
    )
    purpose = purpose_match.group(1) if purpose_match else ""

    # Subject: strip common lead-in phrasing and the purpose/style/size words.
    subject = re.sub(
        r"\b(create|make|design|generate|an?|the|for|about|poster|flyer|worksheet|"
        r"logo|icon|notes?|essay|report|a4|a3|a5|letter|portrait|landscape|"
        r"colorful|colourful|minimal(?:ist)?|clean|playful|professional|scientific|"
        r"in|and|style|please)\b",
        " ",
        lower,
    )
    subject = re.sub(r"\s+", " ", subject).strip(" .,:;-") or None

    if previous:
        subject = previous.get("subject") or subject
        purpose = previous.get("purpose") or purpose

    missing_fields = []
    if not subject:
        missing_fields.append("subject")
    if not purpose:
        missing_fields.append("purpose")

    clarification_question = None
    if "subject" in missing_fields:
        clarification_question = "What topic or subject should the design be about?"
    elif "purpose" in missing_fields:
        clarification_question = (
            "What kind of design do you need — a poster, flyer, worksheet, logo, or notes?"
        )

    workflow_type = _select_workflow_type(purpose) if purpose else "poster_artwork"

    return {
        "subject": subject or "",
        "purpose": purpose or "",
        "size": size,
        "orientation": orientation,
        "style": style,
        "workflow_type": workflow_type,
        "missing_fields": missing_fields,
        "clarification_question": clarification_question,
    }


def _llm_parse(raw_text: str, previous: dict | None) -> dict:
    user_prompt = raw_text
    if previous:
        user_prompt = (
            f"Original request: {previous.get('_raw', raw_text)}\n"
            f"Student's clarification answer: {raw_text}\n"
            f"Previously known fields: {json.dumps(previous)}"
        )
    raw_json = groq_client.chat_json(SYSTEM_PROMPT, user_prompt)
    data = json.loads(raw_json)

    if data.get("workflow_type") not in WORKFLOW_TYPES:
        data["workflow_type"] = _select_workflow_type(data.get("purpose", ""))
    data.setdefault("missing_fields", [])
    data.setdefault("clarification_question", None)
    return data


def understand_request(raw_text: str, previous: dict | None = None) -> dict:
    """Returns a structured_info dict. `previous` is the prior structured_info
    when this call is a clarification follow-up."""
    try:
        result = _llm_parse(raw_text, previous)
        result["_source"] = "groq"
    except groq_client.GroqNotConfigured:
        result = _stub_parse(raw_text, previous)
        result["_source"] = "stub"
    except Exception:
        logger.exception("Groq parse failed, falling back to stub parser")
        result = _stub_parse(raw_text, previous)
        result["_source"] = "stub"

    result["_raw"] = previous.get("_raw", raw_text) if previous else raw_text
    return result


def needs_clarification(structured_info: dict) -> bool:
    return bool(structured_info.get("clarification_question")) and bool(
        structured_info.get("missing_fields")
    )
