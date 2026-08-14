"""Unit tests for evidence retrieval (Step 4A).

All tests are fully offline: httpx.MockTransport injects deterministic
responses, and the PDF fixtures are generated in-memory. No real network
calls are made.

TEST FIXTURES: made-up domains/content used only to exercise retrieval
logic. These are NOT UniHack data and NOT real manufacturer data.
"""

import httpx
import pytest

from app.config import settings
from app.core.domain import Evidence, SourceTrustLevel, SourceType
from app.sources import CandidateStatus, SourceCandidate
from app.sources.retrieval import (
    EvidenceRecord,
    ExtractionStatus,
    HtmlFetcher,
    PdfFetcher,
    RetrievalErrorKind,
    RetrievalLimits,
    RetrievalStatus,
    TRUNCATION_MARKER,
    retrieval_limits_from_settings,
    retrieve_candidate,
    truncate_text,
)

# --- TEST FIXTURES (not UniHack data, not real manufacturers) ----------------

SAMPLE_HTML = (
    "<html><head>"
    "<title>Acme M1 Controller</title>"
    "<link rel='canonical' href='https://acme-controls.example/products/m1-canonical'>"
    "</head><body>"
    "<h1>M1 Controller</h1>"
    "<p>The M1 is a 24V industrial controller.</p>"
    "<script>alert('should be dropped');</script>"
    "<style>.hidden{color:red}</style>"
    "</body></html>"
)


def make_pdf_bytes(text: str = "M1 Controller datasheet") -> bytes:
    """Build a minimal valid PDF in memory (no network)."""
    content = f"BT /F1 12 Tf 20 760 Td ({text}) Tj ET"
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
        f"<< /Length {len(content)} >>\nstream\n{content}\nendstream".encode(),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets: list[int] = []
    for index, obj in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{index} 0 obj\n".encode() + obj + b"\nendobj\n"
    xref_pos = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode()
    out += b"0000000000 65535 f \n"
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
        f"startxref\n{xref_pos}\n%%EOF"
    ).encode()
    return bytes(out)


def make_candidate(
    url: str = "https://acme-controls.example/products/m1",
    source_type: SourceType = SourceType.MANUFACTURER_PRODUCT_PAGE,
    status: CandidateStatus = CandidateStatus.ALLOWED,
    reason: str = "",
) -> SourceCandidate:
    return SourceCandidate(
        id="cand-1", url=url, source_type=source_type, status=status,
        rejection_reason=reason,
    )


def small_limits(**overrides) -> RetrievalLimits:
    defaults = dict(
        timeout_seconds=5.0, max_bytes=50_000, max_pdf_bytes=100_000,
        user_agent="test-agent",
    )
    defaults.update(overrides)
    return RetrievalLimits(**defaults)


def html_ok(request: httpx.Request) -> httpx.Response:
    return httpx.Response(
        200,
        content=SAMPLE_HTML.encode(),
        headers={"content-type": "text/html; charset=utf-8"},
    )


class TestHtmlFetcher:
    def test_successful_html_retrieval(self):
        result = retrieve_candidate(
            make_candidate(),
            fetchers=[HtmlFetcher(transport=httpx.MockTransport(html_ok))],
            limits=small_limits(),
        )
        assert result.retrieval_status == RetrievalStatus.SUCCESS
        assert result.extraction_status == ExtractionStatus.EXTRACTED
        assert result.title == "Acme M1 Controller"
        assert result.content_type == "text/html"
        # canonical URL is preserved as final_url
        assert result.final_url == "https://acme-controls.example/products/m1-canonical"
        # readable text present; script/style dropped
        assert "24V industrial controller" in result.text
        assert "should be dropped" not in result.text
        assert result.source_candidate_id == "cand-1"

    def test_timeout_is_reported(self):
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout("read timed out")

        result = retrieve_candidate(
            make_candidate(),
            fetchers=[HtmlFetcher(transport=httpx.MockTransport(handler))],
            limits=small_limits(),
        )
        assert result.retrieval_status == RetrievalStatus.FAILED
        assert result.error_kind == RetrievalErrorKind.TIMEOUT
        assert "timeout" in result.error_message

    def test_oversized_response_is_rejected(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200, content=b"x" * 100_000, headers={"content-type": "text/html"}
            )

        result = retrieve_candidate(
            make_candidate(),
            fetchers=[HtmlFetcher(transport=httpx.MockTransport(handler))],
            limits=small_limits(max_bytes=1_000),
        )
        assert result.retrieval_status == RetrievalStatus.FAILED
        assert result.error_kind == RetrievalErrorKind.SIZE_LIMIT

    def test_invalid_content_type_is_rejected(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200, content=b"<html>x</html>",
                headers={"content-type": "application/octet-stream"},
            )

        result = retrieve_candidate(
            make_candidate(),
            fetchers=[HtmlFetcher(transport=httpx.MockTransport(handler))],
            limits=small_limits(),
        )
        assert result.retrieval_status == RetrievalStatus.FAILED
        assert result.error_kind == RetrievalErrorKind.INVALID_CONTENT_TYPE

    def test_http_error_status_is_reported(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(404, content="nope")

        result = retrieve_candidate(
            make_candidate(),
            fetchers=[HtmlFetcher(transport=httpx.MockTransport(handler))],
            limits=small_limits(),
        )
        assert result.retrieval_status == RetrievalStatus.FAILED
        assert result.error_kind == RetrievalErrorKind.HTTP_STATUS


class TestPdfFetcher:
    def test_successful_pdf_retrieval(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                content=make_pdf_bytes("M1 Controller datasheet"),
                headers={"content-type": "application/pdf"},
            )

        result = retrieve_candidate(
            make_candidate(
                url="https://acme-controls.example/docs/m1-datasheet.pdf",
                source_type=SourceType.MANUFACTURER_TECHNICAL_PDF,
            ),
            fetchers=[PdfFetcher(transport=httpx.MockTransport(handler))],
            limits=small_limits(),
        )
        assert result.retrieval_status == RetrievalStatus.SUCCESS
        assert result.extraction_status == ExtractionStatus.EXTRACTED
        assert "M1 Controller datasheet" in result.text
        assert result.content_type == "application/pdf"

    def test_pdf_extraction_failure_is_reported(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                content=b"%PDF-1.4\ngarbage that is not a real pdf",
                headers={"content-type": "application/pdf"},
            )

        result = retrieve_candidate(
            make_candidate(
                url="https://acme-controls.example/docs/broken.pdf",
                source_type=SourceType.MANUFACTURER_TECHNICAL_PDF,
            ),
            fetchers=[PdfFetcher(transport=httpx.MockTransport(handler))],
            limits=small_limits(),
        )
        assert result.retrieval_status == RetrievalStatus.FAILED
        assert result.error_kind == RetrievalErrorKind.PDF_EXTRACTION
        assert result.error_message

    def test_non_pdf_signature_rejected(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200, content=b"<html>not a pdf</html>",
                headers={"content-type": "application/pdf"},
            )

        result = retrieve_candidate(
            make_candidate(
                url="https://acme-controls.example/docs/fake.pdf",
                source_type=SourceType.MANUFACTURER_TECHNICAL_PDF,
            ),
            fetchers=[PdfFetcher(transport=httpx.MockTransport(handler))],
            limits=small_limits(),
        )
        assert result.retrieval_status == RetrievalStatus.FAILED
        assert result.error_kind == RetrievalErrorKind.INVALID_CONTENT_TYPE

    def test_empty_text_pdf_reported_as_extraction_failed(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                content=make_pdf_bytes(""),
                headers={"content-type": "application/pdf"},
            )

        result = retrieve_candidate(
            make_candidate(
                url="https://acme-controls.example/docs/scanned.pdf",
                source_type=SourceType.MANUFACTURER_MANUAL,
            ),
            fetchers=[PdfFetcher(transport=httpx.MockTransport(handler))],
            limits=small_limits(),
        )
        assert result.retrieval_status == RetrievalStatus.SUCCESS
        assert result.extraction_status == ExtractionStatus.FAILED
        assert result.error_kind == RetrievalErrorKind.PDF_EXTRACTION


class TestPolicyGate:
    def test_rejected_candidate_is_never_fetched(self):
        def handler(request: httpx.Request) -> httpx.Response:
            raise AssertionError("fetcher must not be called for rejected candidates")

        result = retrieve_candidate(
            make_candidate(
                status=CandidateStatus.PROHIBITED,
                reason="prohibited marketplace domain 'amazon.com'",
            ),
            fetchers=[HtmlFetcher(transport=httpx.MockTransport(handler))],
            limits=small_limits(),
        )
        assert result.retrieval_status == RetrievalStatus.SKIPPED
        assert result.error_kind == RetrievalErrorKind.CANDIDATE_REJECTED
        assert "prohibited marketplace domain" in result.error_message

    def test_unsafe_url_scheme_is_never_fetched(self):
        def handler(request: httpx.Request) -> httpx.Response:
            raise AssertionError("fetcher must not be called for unsafe URLs")

        result = retrieve_candidate(
            make_candidate(url="file:///etc/passwd"),
            fetchers=[HtmlFetcher(transport=httpx.MockTransport(handler))],
            limits=small_limits(),
        )
        assert result.retrieval_status == RetrievalStatus.FAILED
        assert result.error_kind == RetrievalErrorKind.UNSAFE_URL

    def test_no_fetcher_for_source_type(self):
        result = retrieve_candidate(
            make_candidate(source_type=SourceType.MANUFACTURER_TECHNICAL_PDF),
            fetchers=[HtmlFetcher(transport=httpx.MockTransport(html_ok))],
            limits=small_limits(),
        )
        assert result.retrieval_status == RetrievalStatus.FAILED
        assert result.error_kind == RetrievalErrorKind.NO_FETCHER


class TestFallback:
    def test_html_fetcher_falls_back_to_pdf_for_catalogue(self):
        def pdf_response(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                content=make_pdf_bytes("Catalogue 2026"),
                headers={"content-type": "application/pdf"},
            )

        candidate = make_candidate(
            url="https://acme-controls.example/catalogue.pdf",
            source_type=SourceType.MANUFACTURER_CATALOGUE,
        )
        # HTML fetcher gets a PDF response -> INVALID_CONTENT_TYPE with
        # content_type=application/pdf -> orchestrator tries the PDF fetcher.
        result = retrieve_candidate(
            candidate,
            fetchers=[
                HtmlFetcher(transport=httpx.MockTransport(pdf_response)),
                PdfFetcher(transport=httpx.MockTransport(pdf_response)),
            ],
            limits=small_limits(),
        )
        assert result.retrieval_status == RetrievalStatus.SUCCESS
        assert "Catalogue 2026" in result.text


class TestLimitsAndMapping:
    def test_limits_from_settings(self, monkeypatch):
        monkeypatch.setattr(settings, "retrieval_max_bytes", 123_456)
        monkeypatch.setattr(settings, "retrieval_timeout_seconds", 7.5)
        limits = retrieval_limits_from_settings()
        assert limits.max_bytes == 123_456
        assert limits.timeout_seconds == 7.5

    def test_evidence_record_maps_to_domain_evidence(self):
        record = EvidenceRecord(
            evidence_id="ev-abc",
            source_candidate_id="cand-1",
            url="https://acme-controls.example/products/m1",
            source_type=SourceType.MANUFACTURER_PRODUCT_PAGE,
            title="Acme M1 Controller",
            text="The M1 is a 24V controller.",
        )
        evidence = record.to_domain_evidence()
        assert isinstance(evidence, Evidence)
        assert evidence.id == "ev-abc"
        assert evidence.source_url == record.url
        assert evidence.source_type == SourceType.MANUFACTURER_PRODUCT_PAGE
        assert evidence.source_title == "Acme M1 Controller"
        assert evidence.trust_level == SourceTrustLevel.UNVERIFIED


class TestTextCharCap:
    """The extracted-text cap (RETRIEVAL_MAX_TEXT_CHARS) bounds the stored
    evidence text AFTER HTML/PDF extraction, independent of the raw byte caps.
    """

    def test_truncate_text_keeps_head_and_marks_omission(self):
        text = "A" * 1000
        capped = truncate_text(text, 200)
        assert capped.startswith("A" * 200)
        assert "truncated" in capped
        assert len(capped) > 200 and len(capped) < 1000

    def test_truncate_text_is_noop_when_under_cap(self):
        assert truncate_text("short", 200) == "short"
        assert truncate_text("anything", None) == "anything"

    def test_limits_from_settings_carries_max_text_chars(self, monkeypatch):
        monkeypatch.setattr(settings, "retrieval_max_text_chars", 7_777)
        limits = retrieval_limits_from_settings()
        assert limits.max_text_chars == 7_777

    def test_html_text_is_capped_after_extraction(self):
        big = "<html><body><p>" + ("M1 controller spec line. " * 1000) + "</p></body></html>"

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200, content=big.encode(),
                headers={"content-type": "text/html"},
            )

        result = retrieve_candidate(
            make_candidate(),
            fetchers=[HtmlFetcher(transport=httpx.MockTransport(handler))],
            limits=small_limits(max_text_chars=200),
        )
        assert result.retrieval_status == RetrievalStatus.SUCCESS
        assert "truncated" in result.text
        # head preserved, total bounded by cap + marker
        assert result.text.startswith("M1 controller spec line")
        assert len(result.text) <= 200 + len(TRUNCATION_MARKER) + 20

    def test_pdf_text_is_capped_after_extraction(self):
        long_text = "M1 datasheet line. " * 500

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                content=make_pdf_bytes(long_text),
                headers={"content-type": "application/pdf"},
            )

        result = retrieve_candidate(
            make_candidate(
                url="https://acme-controls.example/docs/m1.pdf",
                source_type=SourceType.MANUFACTURER_TECHNICAL_PDF,
            ),
            fetchers=[PdfFetcher(transport=httpx.MockTransport(handler))],
            limits=small_limits(max_text_chars=200, max_pdf_bytes=100_000),
        )
        assert result.retrieval_status == RetrievalStatus.SUCCESS
        assert "truncated" in result.text
        assert len(result.text) <= 200 + len(TRUNCATION_MARKER) + 20
