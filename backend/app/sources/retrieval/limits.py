"""Configurable retrieval limits (timeouts, size caps, user agent).

Follows the project's configuration style: values come from app/config.py
environment settings, with sane defaults here. Per-call overrides are
possible by constructing RetrievalLimits directly.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.config import settings


class RetrievalLimits(BaseModel):
    timeout_seconds: float = Field(default=20.0, gt=0)
    max_bytes: int = Field(default=5_000_000, gt=0)  # HTML responses
    max_pdf_bytes: int = Field(default=25_000_000, gt=0)
    user_agent: str = "ProductTruthEngine/0.1 (hackathon)"
    # Cap on extracted-readable-text per record (characters), applied AFTER
    # HTML/PDF extraction. None disables the cap.
    max_text_chars: int | None = Field(default=None, gt=0)


def retrieval_limits_from_settings() -> RetrievalLimits:
    return RetrievalLimits(
        timeout_seconds=settings.retrieval_timeout_seconds,
        max_bytes=settings.retrieval_max_bytes,
        max_pdf_bytes=settings.retrieval_max_pdf_bytes,
        user_agent=settings.retrieval_user_agent,
        max_text_chars=settings.retrieval_max_text_chars,
    )


# Marker appended to truncated evidence text; keeps the head (most relevant
# product metadata/title) and records how much was omitted.
TRUNCATION_MARKER = "\n... [truncated: {omitted} chars omitted] ..."


def truncate_text(text: str, max_text_chars: int | None) -> str:
    """Cap extracted text to ``max_text_chars`` keeping the head.

    Returns the text unchanged when ``max_text_chars`` is None/<=0 or the text
    is already within the cap.
    """
    if not max_text_chars or max_text_chars <= 0 or len(text) <= max_text_chars:
        return text
    omitted = len(text) - max_text_chars
    return text[:max_text_chars] + TRUNCATION_MARKER.format(omitted=omitted)
