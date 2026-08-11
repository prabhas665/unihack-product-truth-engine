"""PDF evidence fetcher: retrieve an approved PDF URL and extract text.

Uses httpx for transport and pypdf for text extraction. No OCR/vision:
scanned (image-only) PDFs are reported as extraction failures, not guessed.
"""

from __future__ import annotations

import io

import httpx
from pypdf import PdfReader

from app.core.domain import SourceType
from app.sources.candidates import SourceCandidate
from app.sources.retrieval.limits import RetrievalLimits
from app.sources.retrieval.models import (
    EvidenceRecord,
    ExtractionStatus,
    RetrievalError,
    RetrievalErrorKind,
    RetrievalStatus,
)
from app.sources.retrieval.transport import download

PDF_SOURCE_TYPES = frozenset(
    {
        SourceType.MANUFACTURER_TECHNICAL_PDF,
        SourceType.MANUFACTURER_MANUAL,
        SourceType.MANUFACTURER_CATALOGUE,
        SourceType.UNKNOWN,
    }
)

# PDF signature: every valid PDF starts with "%PDF-".
PDF_MAGIC = b"%PDF-"


class PdfFetcher:
    """Retrieves an approved PDF URL and extracts its text layer."""

    name = "pdf"
    supported_types = PDF_SOURCE_TYPES

    def __init__(self, transport: httpx.BaseTransport | None = None) -> None:
        # transport injection keeps tests fully offline (httpx.MockTransport).
        self._transport = transport

    def supports(self, candidate: SourceCandidate) -> bool:
        return candidate.source_type in self.supported_types

    def fetch(
        self, candidate: SourceCandidate, limits: RetrievalLimits
    ) -> EvidenceRecord:
        content_type, final_url, body = download(
            candidate.url, limits, limits.max_pdf_bytes, self._transport
        )
        if not body.startswith(PDF_MAGIC):
            raise RetrievalError(
                RetrievalErrorKind.INVALID_CONTENT_TYPE,
                f"not a PDF (signature check failed) for {candidate.url}",
            )

        try:
            reader = PdfReader(io.BytesIO(body))
            pages = [page.extract_text() or "" for page in reader.pages]
            text = "\n\n".join(page for page in pages if page.strip())
        except Exception as exc:
            raise RetrievalError(
                RetrievalErrorKind.PDF_EXTRACTION,
                f"PDF text extraction failed for {candidate.url}: {exc}",
            ) from exc

        metadata: dict[str, str] = {}
        if reader.metadata:
            for key, value in reader.metadata.items():
                if value is not None:
                    metadata[str(key)] = str(value)
        title = metadata.get("/Title", "")

        return EvidenceRecord(
            source_candidate_id=candidate.id or candidate.url,
            url=candidate.url,
            final_url=final_url,
            source_type=candidate.source_type,
            title=title,
            text=text,
            content_type=content_type,
            retrieval_status=RetrievalStatus.SUCCESS,
            extraction_status=(
                ExtractionStatus.EXTRACTED if text.strip() else ExtractionStatus.FAILED
            ),
            error_kind=(
                None if text.strip() else RetrievalErrorKind.PDF_EXTRACTION
            ),
            error_message=(
                ""
                if text.strip()
                else "no extractable text (possibly a scanned/image-only PDF)"
            ),
            metadata=metadata,
        )
