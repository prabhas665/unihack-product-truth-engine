"""Evidence retrieval package.

Retrieves ALREADY-APPROVED source candidates (those that passed SourcePolicy)
and produces structured EvidenceRecord results: HTML fetcher + PDF fetcher
behind a common Fetcher interface, with hard security/limits gates.

Strictly separated from source discovery (app.sources), AI extraction,
validation, and description generation.
"""

from app.sources.retrieval.base import Fetcher
from app.sources.retrieval.html import (
    HTML_SOURCE_TYPES,
    HtmlFetcher,
    extract_canonical_url,
    extract_html_text,
    extract_html_title,
)
from app.sources.retrieval.limits import (
    RetrievalLimits,
    TRUNCATION_MARKER,
    retrieval_limits_from_settings,
    truncate_text,
)
from app.sources.retrieval.models import (
    EvidenceRecord,
    ExtractionStatus,
    RetrievalError,
    RetrievalErrorKind,
    RetrievalStatus,
)
from app.sources.retrieval.orchestrator import default_fetchers, retrieve_candidate
from app.sources.retrieval.pdf import PDF_SOURCE_TYPES, PdfFetcher

__all__ = [
    "EvidenceRecord",
    "ExtractionStatus",
    "Fetcher",
    "HTML_SOURCE_TYPES",
    "HtmlFetcher",
    "PDF_SOURCE_TYPES",
    "PdfFetcher",
    "RetrievalError",
    "RetrievalErrorKind",
    "RetrievalLimits",
    "RetrievalStatus",
    "TRUNCATION_MARKER",
    "truncate_text",
    "default_fetchers",
    "extract_canonical_url",
    "extract_html_text",
    "extract_html_title",
    "retrieval_limits_from_settings",
    "retrieve_candidate",
]
