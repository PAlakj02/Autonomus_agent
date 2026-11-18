"""Safety check stage.

PLACEHOLDER: set MODERATION_API_KEY in .env and replace `_call_real_api` with
a real moderation provider (e.g. OpenAI omni-moderation, Llama Guard via Groq,
Google's Perspective API). Until then, a conservative keyword-based stub
blocks obviously unsafe requests so the pipeline enforces the safety gate
end-to-end during development.
"""
import logging
import re
from dataclasses import dataclass, field

from app.config import settings

logger = logging.getLogger(__name__)

BANNED_KEYWORDS = [
    "kill", "suicide", "self-harm", "self harm", "weapon", "bomb", "explosive",
    "gun", "porn", "nude", "naked", "sex", "drug", "cocaine", "heroin",
    "terrorist", "hate speech", "racial slur", "gore",
]


@dataclass
class ModerationResult:
    allowed: bool
    reason: str | None = None
    flagged_terms: list[str] = field(default_factory=list)
    source: str = "stub"


def _call_real_api(text: str) -> ModerationResult:
    # PLACEHOLDER: wire up a real moderation provider here using
    # settings.moderation_api_key, e.g.:
    #
    #   resp = requests.post(
    #       "https://api.openai.com/v1/moderations",
    #       headers={"Authorization": f"Bearer {settings.moderation_api_key}"},
    #       json={"input": text},
    #   )
    #   flagged = resp.json()["results"][0]["flagged"]
    #   return ModerationResult(allowed=not flagged, source="real")
    raise NotImplementedError


def _stub_check(text: str) -> ModerationResult:
    lower = text.lower()
    # Whole-word match so e.g. "skill" doesn't trip "kill" or "begun" trip "gun".
    hits = [kw for kw in BANNED_KEYWORDS if re.search(rf"\b{re.escape(kw)}s?\b", lower)]
    if hits:
        return ModerationResult(
            allowed=False,
            reason="Request contains disallowed content for a student platform.",
            flagged_terms=hits,
            source="stub",
        )
    return ModerationResult(allowed=True, source="stub")


def moderate_text(text: str) -> ModerationResult:
    if not settings.is_placeholder(settings.moderation_api_key):
        try:
            return _call_real_api(text)
        except NotImplementedError:
            logger.warning("MODERATION_API_KEY set but _call_real_api not implemented; using stub")
        except Exception:
            logger.exception("Real moderation call failed, falling back to stub")
    return _stub_check(text)


def moderate_generated_output(description: str) -> ModerationResult:
    """Moderates the generated design before preview. For image outputs this
    checks the textual prompt/description used to generate it; swap in a real
    vision-moderation call on the actual asset when a provider is configured."""
    return moderate_text(description)
