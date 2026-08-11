"""Retrieval orchestration: policy gate -> scheme gate -> fetcher dispatch.

Only candidates that passed SourcePolicy (status ALLOWED) are ever fetched.
Rejected/prohibited candidates yield a SKIPPED record carrying the rejection
reason - they are never retrieved. No new sources are discovered here.
"""

from __future__ import annotations

from urllib.parse import urlsplit

from app.sources.candidates import CandidateStatus, SourceCandidate
from app.sources.retrieval.base import Fetcher
from app.sources.retrieval.html import HtmlFetcher
from app.sources.retrieval.limits import RetrievalLimits, retrieval_limits_from_settings
from app.sources.retrieval.models import (
    EvidenceRecord,
    RetrievalError,
    RetrievalErrorKind,
    RetrievalStatus,
)
from app.sources.retrieval.pdf import PdfFetcher


def default_fetchers() -> list[Fetcher]:
    return [HtmlFetcher(), PdfFetcher()]


def retrieve_candidate(
    candidate: SourceCandidate,
    fetchers: list[Fetcher] | None = None,
    limits: RetrievalLimits | None = None,
) -> EvidenceRecord:
    """Fetch one approved candidate and return its structured evidence record.

    Never raises for fetch failures: typed RetrievalErrors are captured into
    the record's error fields so nothing is swallowed silently.
    """
    limits = limits or retrieval_limits_from_settings()
    fetchers = list(fetchers) if fetchers is not None else default_fetchers()

    if candidate.status != CandidateStatus.ALLOWED:
        return _record(
            candidate,
            retrieval_status=RetrievalStatus.SKIPPED,
            error_kind=RetrievalErrorKind.CANDIDATE_REJECTED,
            error_message=(
                f"candidate not allowed: {candidate.status.value} - "
                f"{candidate.rejection_reason or 'no reason recorded'}"
            ),
        )

    if urlsplit(candidate.url).scheme not in ("http", "https"):
        return _record(
            candidate,
            retrieval_status=RetrievalStatus.FAILED,
            error_kind=RetrievalErrorKind.UNSAFE_URL,
            error_message=f"unsafe URL scheme for {candidate.url}",
        )

    for fetcher in fetchers:
        if not fetcher.supports(candidate):
            continue
        try:
            return fetcher.fetch(candidate, limits)
        except RetrievalError as exc:
            # Catalogue/unknown candidates may be PDFs: if the HTML fetcher
            # received a PDF, let the next fetcher (PDF) try.
            if (
                exc.kind == RetrievalErrorKind.INVALID_CONTENT_TYPE
                and exc.content_type == "application/pdf"
            ):
                continue
            return _record(candidate, retrieval_status=RetrievalStatus.FAILED,
                           error_kind=exc.kind, error_message=exc.message)

    return _record(
        candidate,
        retrieval_status=RetrievalStatus.FAILED,
        error_kind=RetrievalErrorKind.NO_FETCHER,
        error_message=f"no fetcher supports source type {candidate.source_type.value}",
    )


def _record(
    candidate: SourceCandidate,
    *,
    retrieval_status: RetrievalStatus,
    error_kind: RetrievalErrorKind,
    error_message: str,
) -> EvidenceRecord:
    return EvidenceRecord(
        source_candidate_id=candidate.id or candidate.url,
        url=candidate.url,
        source_type=candidate.source_type,
        retrieval_status=retrieval_status,
        error_kind=error_kind,
        error_message=error_message,
    )
