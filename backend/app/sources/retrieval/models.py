"""Evidence retrieval models.

A retrieved EvidenceRecord is the raw result of fetching an approved source
candidate - content plus status/error metadata. It is deliberately distinct
from the domain Evidence (app.core.domain), which carries quotations
attached to attributes. AI extraction, validation, and description
generation are separate stages and NOT part of this module.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field

from app.core.domain import Evidence, SourceTrustLevel, SourceType
from app.core.domain.common import utcnow


class RetrievalStatus(str, Enum):
    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"  # never attempted (e.g. candidate not allowed)


class ExtractionStatus(str, Enum):
    EXTRACTED = "extracted"
    PARTIAL = "partial"
    FAILED = "failed"
    NOT_APPLICABLE = "not_applicable"


class RetrievalErrorKind(str, Enum):
    UNSAFE_URL = "unsafe_url"
    CANDIDATE_REJECTED = "candidate_rejected"
    NETWORK = "network"
    TIMEOUT = "timeout"
    SIZE_LIMIT = "size_limit"
    INVALID_CONTENT_TYPE = "invalid_content_type"
    HTTP_STATUS = "http_status"
    HTML_EXTRACTION = "html_extraction"
    PDF_EXTRACTION = "pdf_extraction"
    NO_FETCHER = "no_fetcher"


class RetrievalError(Exception):
    """Typed retrieval failure.

    Carries a kind and a human-readable message. Fetchers raise it; the
    orchestrator turns it into error fields on the EvidenceRecord - errors
    are never silently swallowed.
    """

    def __init__(
        self,
        kind: RetrievalErrorKind,
        message: str,
        *,
        content_type: str = "",
    ) -> None:
        super().__init__(message)
        self.kind = kind
        self.message = message
        self.content_type = content_type


class EvidenceRecord(BaseModel):
    """Structured result of fetching one approved candidate source."""

    evidence_id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    source_candidate_id: str = ""
    url: str = ""  # original approved URL
    final_url: str = ""  # after redirects / canonical link
    source_type: SourceType = SourceType.UNKNOWN
    title: str = ""
    text: str = ""  # extracted readable text
    content_type: str = ""
    retrieved_at: datetime = Field(default_factory=utcnow)
    retrieval_status: RetrievalStatus = RetrievalStatus.SUCCESS
    extraction_status: ExtractionStatus = ExtractionStatus.NOT_APPLICABLE
    error_kind: RetrievalErrorKind | None = None
    error_message: str = ""
    metadata: dict[str, str] = Field(default_factory=dict)

    def to_domain_evidence(self) -> Evidence:
        """Map into the domain Evidence model for the future pipeline.

        trust_level is UNVERIFIED here; the pipeline re-derives it from the
        candidate's policy outcome before attaching evidence to attributes.
        """
        return Evidence(
            id=self.evidence_id,
            source_url=self.url,
            source_type=self.source_type,
            source_title=self.title or self.url,
            snippet="",
            retrieved_at=self.retrieved_at,
            trust_level=SourceTrustLevel.UNVERIFIED,
            supports_attributes=[],
        )
