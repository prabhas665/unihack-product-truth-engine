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
    max_pdf_bytes: int = Field(default=10_000_000, gt=0)
    user_agent: str = "ProductTruthEngine/0.1 (hackathon)"


def retrieval_limits_from_settings() -> RetrievalLimits:
    return RetrievalLimits(
        timeout_seconds=settings.retrieval_timeout_seconds,
        max_bytes=settings.retrieval_max_bytes,
        max_pdf_bytes=settings.retrieval_max_pdf_bytes,
        user_agent=settings.retrieval_user_agent,
    )
