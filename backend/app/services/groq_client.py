"""Thin wrapper around the Groq API used for the 'understand the request' stage.

PLACEHOLDER: set GROQ_API_KEY in .env to enable real LLM parsing. Until then
(or if the `groq` package / network call fails), callers fall back to the
rule-based stub in app/services/parser.py so the rest of the pipeline stays
testable end to end.
"""
import logging

from app.config import settings

logger = logging.getLogger(__name__)

_client = None


class GroqNotConfigured(Exception):
    pass


def _get_client():
    global _client
    if _client is not None:
        return _client

    if settings.is_placeholder(settings.groq_api_key):
        raise GroqNotConfigured("GROQ_API_KEY is still a placeholder")

    try:
        from groq import Groq
    except ImportError as exc:
        raise GroqNotConfigured("groq package not installed") from exc

    _client = Groq(api_key=settings.groq_api_key)
    return _client


def chat_json(system_prompt: str, user_prompt: str) -> str:
    """Runs a chat completion constrained to JSON output. Raises GroqNotConfigured
    if no real key is set, so callers can fall back to a stub."""
    client = _get_client()
    response = client.chat.completions.create(
        model=settings.groq_model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.2,
        response_format={"type": "json_object"},
    )
    return response.choices[0].message.content


def chat_text(system_prompt: str, user_prompt: str) -> str:
    client = _get_client()
    response = client.chat.completions.create(
        model=settings.groq_model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.6,
    )
    return response.choices[0].message.content
