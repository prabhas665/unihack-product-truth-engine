"""Offline orchestration tests for the single-product enrichment pipeline
(Step 6D): EnrichmentService + POST /api/enrich.

Everything is fake/mocked: search provider, retrieval, and the LLM provider
(httpx.MockTransport-style fakes and direct callable/object injection). No
network calls and no real provider credentials are ever used.
"""

from __future__ import annotations

import csv
import hashlib
import json

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.core.domain import ProcessingStatus, SourceType
from app.extraction import ExtractionOutput, ExtractionOutputItem
from app.api.routes.enrich import get_enrichment_service
from app.llm import (
    LLMClient,
    LLMConfigurationError,
    LLMInvalidResponseError,
    LLMProviderUnavailableError,
    LLMTimeoutError,
)
from app.main import app
from app.pipeline.enrichment import (
    EnrichmentRequest,
    EnrichmentService,
    StageName,
    StageStatus,
)
from app.sources.candidates import DiscoveryMethod, SourceCandidate
from app.sources.errors import ProviderUnavailableError
from app.sources.retrieval import (
    EvidenceRecord,
    ExtractionStatus,
    RetrievalErrorKind,
    RetrievalStatus,
)
from app.unihack.schema import DeliverySchema

ACME_PAGE = "https://www.acme.com/products/dcb518asts06g"
ACME_PDF = "https://www.acme.com/docs/dcb518asts06g.pdf"
EVIDENCE_A = "ev-acme-page-0001"


# --------------------------------------------------------------------------
# fakes
# --------------------------------------------------------------------------


class FakeProvider:
    """A discovery provider that returns canned candidates (or fails)."""

    name = "fake-search"
    kind = DiscoveryMethod.SEARCH

    def __init__(
        self,
        candidates: list[SourceCandidate] | None = None,
        error: Exception | None = None,
    ) -> None:
        self._candidates = candidates or []
        self._error = error

    def discover(self, product, context):
        if self._error is not None:
            raise self._error
        return list(self._candidates)


class FakeRetriever:
    """Returns canned EvidenceRecords keyed by candidate URL."""

    def __init__(self, records: list[EvidenceRecord]) -> None:
        self.by_url = {record.url: record for record in records}
        self.calls: list[str] = []

    def __call__(self, candidate: SourceCandidate) -> EvidenceRecord:
        self.calls.append(candidate.url)
        return self.by_url.get(candidate.url, failed_record(candidate.url))


DESCRIPTIONS_JSON = json.dumps(
    {
        "product_title": "DCB518ASTS06G Sanding Belt 6-Pack",
        "short_description": "Six-pack of 1/2 x 18 inch sanding belts.",
        "mobile_description": "Diablo 1/2x18 in sanding belt, 6 pack.",
        "invoice_description": "Sanding belt 1/2x18 in, pack of 6.",
        "long_description": (
            "A six-pack of Diablo sanding belts, each 1/2 inch wide and 18 "
            "inches long, for belt sanders."
        ),
        "retail_description": "Diablo 1/2 in x 18 in sanding belt, 6 pack.",
        "marketing_description": "Professional sanding belts in a 6-pack.",
        "item_features": ["1/2 inch width", "18 inch length", "pack of 6"],
        "with": "Six sanding belts",
        "application": "Belt sanders",
        "includes": "6 sanding belts",
        "product_name": "Sanding Belt",
    }
)


class FakeLLMClient(LLMClient):
    """An LLMClient returning canned JSON (or raising a typed LLM error)."""

    provider = "fake"

    def __init__(
        self,
        output: ExtractionOutput | None = None,
        raw: str = "",
        error: Exception | None = None,
        error_on_description: Exception | None = None,
    ) -> None:
        self._output = output
        self._raw = raw
        self._error = error
        self._error_on_description = error_on_description

    def _complete(
        self,
        prompt: str,
        *,
        system_prompt: str = "",
        temperature: float | None = None,
        timeout_seconds: float | None = None,
    ) -> str:
        if self._error is not None:
            raise self._error
        if "PRODUCT IDENTITY" in prompt:
            if self._error_on_description is not None:
                raise self._error_on_description
            return DESCRIPTIONS_JSON
        if self._raw:
            return self._raw
        return json.dumps(self._output.model_dump())


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


def candidate(
    url: str,
    *,
    source_type: SourceType = SourceType.MANUFACTURER_PRODUCT_PAGE,
) -> SourceCandidate:
    return SourceCandidate(
        id=f"cand-{hashlib.sha256(url.encode()).hexdigest()[:12]}",
        url=url,
        source_type=source_type,
        title=url,
    )


def success_record(
    url: str,
    *,
    evidence_id: str,
    text: str = "Diablo sanding belt, 1/2 inch x 18 inch, pack of 6.",
) -> EvidenceRecord:
    return EvidenceRecord(
        evidence_id=evidence_id,
        source_candidate_id=f"cand-{url}",
        url=url,
        source_type=SourceType.MANUFACTURER_PRODUCT_PAGE,
        title=url,
        text=text,
        content_type="text/html",
        retrieval_status=RetrievalStatus.SUCCESS,
        extraction_status=ExtractionStatus.EXTRACTED,
    )


def failed_record(
    url: str,
    *,
    kind: RetrievalErrorKind = RetrievalErrorKind.NETWORK,
    message: str = "connection refused",
) -> EvidenceRecord:
    return EvidenceRecord(
        source_candidate_id=f"cand-{url}",
        url=url,
        source_type=SourceType.MANUFACTURER_PRODUCT_PAGE,
        retrieval_status=RetrievalStatus.FAILED,
        error_kind=kind,
        error_message=message,
    )


def default_request(**overrides) -> EnrichmentRequest:
    """The real dataset row 1 (MPN DCB518ASTS06G), placeholders verbatim."""
    payload = {
        "Mfg_Part_Num": "DCB518ASTS06G",
        "Part_Desc": 'DCB518ASTS06G Diablo 1/2"x18" - Sanding Belt 6pc',
        "E1_Brand": "-- Unbranded --",
        "Unilog_Brand": "-- No Unilog Brand --",
        "DIB_Brand": "-- No DIB Brand --",
        "Part_Manuf": "Freud Inc (2435)",
    }
    payload.update(overrides)
    return EnrichmentRequest(**payload)


def canned_output(evidence_id: str = EVIDENCE_A) -> ExtractionOutput:
    return ExtractionOutput(
        items=[
            ExtractionOutputItem(
                name="belt_width",
                raw_value="0.5 inch",
                normalized_value="0.5 in",
                unit="in",
                confidence=0.9,
                evidence_ids=[evidence_id],
                notes="from the product page",
            ),
            ExtractionOutputItem(
                name="belt_length",
                raw_value="18 inch",
                normalized_value="18 in",
                unit="in",
                confidence=0.85,
                evidence_ids=[evidence_id],
                notes="from the product page",
            ),
        ]
    )


def make_service(
    *,
    provider: FakeProvider | None = None,
    records: list[EvidenceRecord] | None = None,
    llm: FakeLLMClient | None = None,
    manufacturer_domains: list[str] | None = None,
    retriever: FakeRetriever | None = None,
) -> EnrichmentService:
    return EnrichmentService(
        providers=[provider] if provider is not None else [],
        manufacturer_domains=manufacturer_domains or ["acme.com"],
        retriever=retriever if retriever is not None else FakeRetriever(records or []),
        llm_client=llm,
    )


def acme_provider() -> FakeProvider:
    return FakeProvider([candidate(ACME_PAGE), candidate(ACME_PDF)])


def acme_evidence() -> list[EvidenceRecord]:
    return [
        success_record(ACME_PAGE, evidence_id=EVIDENCE_A),
        success_record(ACME_PDF, evidence_id="ev-acme-pdf-0002"),
    ]


def delivery_schema() -> DeliverySchema:
    return DeliverySchema.frozen()


def column(schema: DeliverySchema, name: str) -> int:
    return schema.index_of(name)


def success_service(**kwargs) -> EnrichmentService:
    kwargs.setdefault("provider", acme_provider())
    kwargs.setdefault("records", acme_evidence())
    kwargs.setdefault("llm", FakeLLMClient(output=canned_output()))
    return make_service(**kwargs)


# --------------------------------------------------------------------------
# pipeline tests
# --------------------------------------------------------------------------


class TestHappyPath:
    def test_full_pipeline_success(self):
        result = success_service().run(default_request())

        assert result.processing.status == ProcessingStatus.COMPLETED
        assert [s.stage for s in result.stages] == list(StageName)
        for state in result.stages:
            assert state.status == StageStatus.COMPLETED, state

        assert result.discovery.total_discovered == 2
        assert len(result.discovery.candidates) == 2
        assert len(result.evidence) == 2
        assert result.extraction is not None
        assert len(result.extraction.attributes) == 2
        assert result.extraction.evidence_ids_used == [EVIDENCE_A]
        assert result.validation is not None
        assert result.product is not None
        assert result.product.descriptions.product_title == (
            "DCB518ASTS06G Sanding Belt 6-Pack"
        )
        assert len(result.product.descriptions.item_features) == 3

        assert result.delivery.column_count == 252
        assert len(result.delivery.values) == 252
        assert len(result.delivery.headers) == 252

        schema = delivery_schema()
        values = result.delivery.values
        assert values[column(schema, "Mfg_Part_Num")] == "DCB518ASTS06G"
        assert values[column(schema, "Part_Desc")] == default_request().Part_Desc
        assert values[column(schema, "PART_NUMBER")] == "DCB518ASTS06G"
        assert values[column(schema, "MANUFACTURER_NAME")] == "Freud"
        assert values[column(schema, "MFR URL")] == ACME_PAGE
        assert values[column(schema, "Ref URL 1")] == ACME_PDF
        # Generated descriptions land in the official delivery columns,
        # after UniHack rule enforcement (unit normalization + X spacing).
        assert values[column(schema, "MOBILE_DESC")] == (
            "Diablo 1/2 x 18 IN. sanding belt, 6 pack."
        )
        assert values[column(schema, "INVOICE_DESC")] == (
            "SANDING BELT 1/2 X 18 IN. PACK OF 6"
        )
        assert values[column(schema, "SHORT_DESC")] == (
            "Six-pack of 1/2 x 18 inch sanding belts."
        )
        assert values[column(schema, "LONG_DESC1")] == (
            "A six-pack of Diablo sanding belts, each 1/2 inch wide and 18 "
            "inches long, for belt sanders."
        )
        assert values[column(schema, "RETAIL_DESC")] == (
            "Diablo 1/2 in x 18 in sanding belt, 6 pack."
        )
        assert values[column(schema, "MARKETING_DESCRIPTION")] == (
            "Professional sanding belts in a 6-pack."
        )
        assert values[column(schema, "ITEM_FEATURES_1")] == "1/2 inch width"
        assert values[column(schema, "ITEM_FEATURES_2")] == "18 inch length"
        assert values[column(schema, "ITEM_FEATURES_3")] == "pack of 6"
        assert values[column(schema, "With")] == "Six sanding belts"
        assert values[column(schema, "Application")] == "Belt sanders"
        assert values[column(schema, "Includes")] == "6 sanding belts"
        assert values[column(schema, "Product Name")] == "Sanding Belt"

        label, value, uom = schema.attribute_slots()[0]
        assert values[column(schema, label)] == "belt_width"
        assert values[column(schema, value)] == "0.5 in"
        assert values[column(schema, uom)] == "in"

    def test_stage_transitions_in_order(self):
        transitions: list[tuple[StageName, StageStatus]] = []

        success_service().run(
            default_request(),
            on_stage=lambda name, status: transitions.append((name, status)),
        )

        for name in StageName:
            assert (name, StageStatus.RUNNING) in transitions
            assert (name, StageStatus.COMPLETED) in transitions
        order = [entry[0] for entry in transitions if entry[1] == StageStatus.RUNNING]
        assert order == list(StageName)

    def test_validation_unavailable_stays_not_validated(self):
        result = success_service().run(default_request())

        assert result.validation.counts == {"not_validated": 2}
        assert "verified" not in result.validation.counts
        assert all(
            attribute.outcome.value == "not_validated"
            for attribute in result.validation.attributes
        )
        # BRAND_NAME is resolved from the verified seed (Part_Manuf ->
        # Freud), not invented from the input placeholder - never a guessed
        # value.
        schema = delivery_schema()
        assert result.delivery.values[column(schema, "BRAND_NAME")] == "Freud"
        assert any("verified identity" in reason for reason in result.review_reasons)

    def test_quality_metrics_are_honest(self):
        result = success_service().run(default_request())

        assert result.quality.overall == 0.0  # official formula unavailable
        assert result.quality.evidence_coverage == 1.0
        assert result.quality.validation_coverage == 0.0  # nothing VERIFIED
        assert result.quality.confidence.count == 2
        assert result.quality.confidence.mean == pytest.approx(0.875)

    def test_original_input_preserved_verbatim(self):
        result = success_service().run(default_request())

        view = result.input_row
        assert view.mfg_part_num == "DCB518ASTS06G"
        assert view.e1_brand == "-- Unbranded --"
        assert view.e1_brand_value is None
        assert view.part_manuf_value == "Freud Inc (2435)"
        assert "e1_brand" in view.missing_fields
        assert result.request.E1_Brand == "-- Unbranded --"

    def test_delivery_csv_written_with_official_header(self, tmp_path):
        out = tmp_path / "delivery.csv"
        result = success_service().run(default_request(), output_path=out)

        raw = out.read_bytes()
        assert raw.startswith(b"\xef\xbb\xbf")  # UTF-8 BOM like the reference
        with out.open("r", encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.reader(handle))
        assert len(rows) == 2
        assert rows[0] == delivery_schema().headers
        assert rows[1] == result.delivery.values

    def test_writes_to_arbitrary_output_path(self, tmp_path):
        """The delivery row can be written to any caller-chosen CSV path."""
        out = tmp_path / "out.csv"
        result = success_service().run(
            default_request(), output_path=str(out)
        )
        assert out.is_file()
        assert result.delivery.column_count == 252


class _SlowFakeLLMClient(LLMClient):
    """Offline client whose calls sleep past the wall-clock deadline."""

    provider = "fake-slow"

    def __init__(self, sleep_seconds: float = 5.0) -> None:
        self._sleep = sleep_seconds

    def _complete(
        self,
        prompt: str,
        *,
        system_prompt: str = "",
        temperature: float | None = None,
        timeout_seconds: float | None = None,
    ) -> str:
        import time

        time.sleep(self._sleep)
        return "{}"


class TestWallClockTimeout:
    def test_run_never_hangs_when_llm_exceeds_deadline(self, monkeypatch):
        import time

        monkeypatch.setattr(settings, "llm_timeout_seconds", 0.2)
        service = make_service(
            provider=acme_provider(),
            records=acme_evidence(),
            llm=_SlowFakeLLMClient(sleep_seconds=5.0),
        )
        start = time.perf_counter()
        result = service.run(default_request())
        elapsed = time.perf_counter() - start
        # The hard wall-clock fires at 0.2s; the 5s sleep must not block the run.
        assert elapsed < 2.0
        extraction = next(
            s for s in result.stages if s.stage == StageName.EXTRACTION
        )
        # A wall-clock timeout is non-fatal: the stage becomes NEEDS_REVIEW
        # and the evidence + delivery survive (P0 fix).
        assert extraction.status == StageStatus.NEEDS_REVIEW
        assert result.processing.status == ProcessingStatus.NEEDS_REVIEW
        assert result.product is not None
        assert result.product.attributes == {}
        assert result.delivery.column_count == 252


class TestSparseAndFailedRuns:
    def test_no_candidates_skips_retrieval_chain(self):
        result = make_service(
            provider=FakeProvider([]),
            llm=FakeLLMClient(output=canned_output()),
        ).run(default_request())

        assert result.processing.status == ProcessingStatus.NEEDS_REVIEW
        statuses = {s.stage: s.status for s in result.stages}
        assert statuses[StageName.RETRIEVAL] == StageStatus.SKIPPED
        assert statuses[StageName.EXTRACTION] == StageStatus.SKIPPED
        assert statuses[StageName.VALIDATION] == StageStatus.SKIPPED
        assert statuses[StageName.PRODUCT_INTELLIGENCE] == StageStatus.COMPLETED
        assert statuses[StageName.DELIVERY] == StageStatus.COMPLETED
        assert result.extraction is None
        assert result.validation is None
        assert any("no allowed source candidates" in r for r in result.review_reasons)

    def test_all_candidates_rejected_by_policy(self):
        provider = FakeProvider(
            [
                candidate("https://www.amazon.com/dp/B0000001"),
                candidate("https://shop.example.com/item/1"),
            ]
        )
        result = make_service(provider=provider).run(default_request())

        assert result.discovery.candidates == []
        assert len(result.discovery.rejected) == 2
        reasons = [r.rejection_reason for r in result.discovery.rejected]
        assert any("marketplace" in r for r in reasons)
        assert any("unknown external domain" in r for r in reasons)
        assert any("rejected candidate" in r for r in result.review_reasons)
        assert result.processing.status == ProcessingStatus.NEEDS_REVIEW

    def test_provider_error_recorded_not_fatal(self):
        provider = FakeProvider(
            candidates=[candidate(ACME_PAGE)],
            error=ProviderUnavailableError("boom", "search API timed out"),
        )
        result = make_service(
            provider=provider,
            records=acme_evidence(),
            llm=FakeLLMClient(output=canned_output()),
        ).run(default_request())

        assert len(result.discovery.provider_errors) == 1
        assert result.discovery.provider_errors[0].error_kind == "unavailable"
        assert any("search API timed out" in r for r in result.review_reasons)
        # The provider failed, so nothing could be enriched: needs review.
        assert result.processing.status == ProcessingStatus.NEEDS_REVIEW

    def test_retrieval_failure_skips_extraction(self):
        result = make_service(
            provider=acme_provider(),
            records=[failed_record(ACME_PAGE, message="connection refused")],
            llm=FakeLLMClient(output=canned_output()),
        ).run(default_request())

        statuses = {s.stage: s.status for s in result.stages}
        assert statuses[StageName.EXTRACTION] == StageStatus.SKIPPED
        assert statuses[StageName.VALIDATION] == StageStatus.SKIPPED
        assert result.extraction is None
        assert result.evidence[0].retrieval_status == RetrievalStatus.FAILED
        assert result.evidence[0].error_message == "connection refused"
        assert any("connection refused" in r for r in result.review_reasons)
        assert result.processing.status == ProcessingStatus.NEEDS_REVIEW

    def test_partial_retrieval_failure_uses_successful_evidence(self):
        result = make_service(
            provider=acme_provider(),
            records=[
                success_record(ACME_PAGE, evidence_id=EVIDENCE_A),
                failed_record(ACME_PDF, message="pdf fetch failed"),
            ],
            llm=FakeLLMClient(output=canned_output()),
        ).run(default_request())

        assert len(result.evidence) == 2
        statuses = {record.url: record.retrieval_status for record in result.evidence}
        assert statuses[ACME_PAGE] == RetrievalStatus.SUCCESS
        assert statuses[ACME_PDF] == RetrievalStatus.FAILED
        assert result.extraction is not None
        assert result.extraction.evidence_ids_used == [EVIDENCE_A]
        assert any("pdf fetch failed" in r for r in result.review_reasons)
        assert result.processing.status == ProcessingStatus.NEEDS_REVIEW


class TestExtractionFailureModes:
    def test_llm_provider_failure_marks_extraction_failed(self):
        result = make_service(
            provider=acme_provider(),
            records=acme_evidence(),
            llm=FakeLLMClient(error=LLMProviderUnavailableError("boom: provider exploded")),
        ).run(default_request())

        statuses = {s.stage: s.status for s in result.stages}
        assert statuses[StageName.EXTRACTION] == StageStatus.FAILED
        assert statuses[StageName.VALIDATION] == StageStatus.SKIPPED
        assert statuses[StageName.DELIVERY] == StageStatus.COMPLETED
        assert result.extraction is None
        assert any("provider exploded" in r for r in result.review_reasons)
        assert result.processing.status == ProcessingStatus.FAILED
        assert result.processing.errors[0].stage == StageName.EXTRACTION.value

    def test_malformed_llm_output_marks_extraction_failed(self):
        result = make_service(
            provider=acme_provider(),
            records=acme_evidence(),
            llm=FakeLLMClient(raw="this is not json"),
        ).run(default_request())

        statuses = {s.stage: s.status for s in result.stages}
        assert statuses[StageName.EXTRACTION] == StageStatus.FAILED
        assert any("schema" in r for r in result.review_reasons)
        assert result.processing.status == ProcessingStatus.FAILED

    def test_missing_llm_configuration_does_not_crash(self, monkeypatch):
        monkeypatch.setattr(settings, "llm_provider", "")
        monkeypatch.setattr(settings, "llm_api_key", "")
        result = make_service(
            provider=acme_provider(),
            records=acme_evidence(),
            llm=None,
        ).run(default_request())

        statuses = {s.stage: s.status for s in result.stages}
        assert statuses[StageName.EXTRACTION] == StageStatus.FAILED
        assert any("No LLM provider configured" in r for r in result.review_reasons)
        assert result.processing.status == ProcessingStatus.FAILED
        assert result.delivery.column_count == 252

    def test_extraction_timeout_is_needs_review_not_failed(self):
        # A hard wall-clock timeout during extraction must NOT fail the whole
        # run: discovery and retrieval already succeeded, so the evidence,
        # product intelligence, and 252-column delivery survive for review.
        result = make_service(
            provider=acme_provider(),
            records=acme_evidence(),
            llm=FakeLLMClient(
                error=LLMTimeoutError("openrouter: wall-clock timeout after 60s")
            ),
        ).run(default_request())

        statuses = {s.stage: s.status for s in result.stages}
        assert statuses[StageName.EXTRACTION] == StageStatus.NEEDS_REVIEW
        assert statuses[StageName.VALIDATION] == StageStatus.SKIPPED
        assert statuses[StageName.DESCRIPTION] == StageStatus.SKIPPED
        assert statuses[StageName.PRODUCT_INTELLIGENCE] == StageStatus.COMPLETED
        assert statuses[StageName.DELIVERY] == StageStatus.COMPLETED
        # The timeout must NOT fail the whole run.
        assert result.processing.status == ProcessingStatus.NEEDS_REVIEW
        assert result.processing.status != ProcessingStatus.FAILED
        # Clear, exact review reason.
        assert any(
            r == "Extraction unavailable: LLM call timed out."
            for r in result.review_reasons
        )
        # Nothing was extracted, nothing fabricated.
        assert result.extraction is None
        assert result.product is not None
        assert result.product.attributes == {}
        # Retrieved evidence is preserved intact.
        assert len(result.product.evidence) == 2
        # 252-column delivery still produced; attribute slots blank.
        schema = delivery_schema()
        assert result.delivery.column_count == 252
        assert result.delivery.values[column(schema, "Mfg_Part_Num")] == (
            "DCB518ASTS06G"
        )
        assert result.delivery.values[column(schema, "ATTRIBUTE_LABEL 1")] == ""

    def test_extraction_non_timeout_failure_stays_fatal(self):
        # A non-timeout LLM error (provider unavailable) must still fail the
        # run, proving only timeouts became non-fatal.
        result = make_service(
            provider=acme_provider(),
            records=acme_evidence(),
            llm=FakeLLMClient(
                error=LLMProviderUnavailableError("boom: provider exploded")
            ),
        ).run(default_request())

        statuses = {s.stage: s.status for s in result.stages}
        assert statuses[StageName.EXTRACTION] == StageStatus.FAILED
        assert result.processing.status == ProcessingStatus.FAILED

    def test_dangling_evidence_reference_rejected(self):
        output = ExtractionOutput(
            items=[
                ExtractionOutputItem(
                    name="belt_width",
                    raw_value="0.5 inch",
                    confidence=0.9,
                    evidence_ids=["ev-does-not-exist"],
                )
            ]
        )
        result = make_service(
            provider=acme_provider(),
            records=acme_evidence(),
            llm=FakeLLMClient(output=output),
        ).run(default_request())

        assert result.extraction is not None
        assert result.extraction.attributes == []
        assert len(result.extraction.rejected) == 1
        assert "dangling evidence id" in result.extraction.rejected[0].reason
        assert any("dangling" in r for r in result.review_reasons)
        # The product intelligence stays evidence-consistent.
        assert result.product is not None
        assert result.product.attributes == {}

    def test_no_llm_call_without_usable_evidence(self):
        retriever = FakeRetriever(
            [failed_record(ACME_PAGE, message="timeout")]
        )
        service = make_service(
            provider=acme_provider(),
            retriever=retriever,
            llm=FakeLLMClient(output=canned_output()),
        )
        result = service.run(default_request())

        statuses = {s.stage: s.status for s in result.stages}
        assert statuses[StageName.EXTRACTION] == StageStatus.SKIPPED
        assert statuses[StageName.DESCRIPTION] == StageStatus.SKIPPED
        assert set(retriever.calls) == {ACME_PAGE, ACME_PDF}
        assert result.review_reasons  # reasons present, nothing fabricated

    def test_description_llm_failure_marks_stage_failed(self):
        result = make_service(
            provider=acme_provider(),
            records=acme_evidence(),
            llm=FakeLLMClient(
                output=canned_output(),
                error_on_description=LLMProviderUnavailableError(
                    "boom: descriptions provider exploded"
                ),
            ),
        ).run(default_request())

        statuses = {s.stage: s.status for s in result.stages}
        assert statuses[StageName.EXTRACTION] == StageStatus.COMPLETED
        assert statuses[StageName.DESCRIPTION] == StageStatus.FAILED
        assert statuses[StageName.DELIVERY] == StageStatus.COMPLETED
        assert result.product is not None
        assert result.product.descriptions.product_title == ""
        assert any(
            "descriptions provider exploded" in r for r in result.review_reasons
        )
        assert result.processing.status == ProcessingStatus.FAILED

    def test_description_timeout_is_non_fatal(self):
        result = make_service(
            provider=acme_provider(),
            records=acme_evidence(),
            llm=FakeLLMClient(
                output=canned_output(),
                error_on_description=LLMTimeoutError(
                    "openrouter: wall-clock timeout after 60s"
                ),
            ),
        ).run(default_request())

        statuses = {s.stage: s.status for s in result.stages}
        assert statuses[StageName.EXTRACTION] == StageStatus.COMPLETED
        assert statuses[StageName.VALIDATION] == StageStatus.COMPLETED
        assert statuses[StageName.DESCRIPTION] == StageStatus.NEEDS_REVIEW
        assert statuses[StageName.PRODUCT_INTELLIGENCE] == StageStatus.COMPLETED
        assert statuses[StageName.DELIVERY] == StageStatus.COMPLETED
        # The timeout must NOT fail the whole run.
        assert result.processing.status == ProcessingStatus.NEEDS_REVIEW
        assert result.processing.status != ProcessingStatus.FAILED
        # Clear, exact review reason.
        assert any(
            r == (
                "Description generation unavailable: OpenRouter timeout."
            )
            for r in result.review_reasons
        )
        # No fabricated copy: description fields stay blank.
        assert result.product is not None
        assert result.product.descriptions.product_title == ""
        assert result.product.descriptions.short_description == ""
        # Extracted attributes and evidence are preserved intact.
        assert len(result.product.attributes) == 2
        assert len(result.product.evidence) == 2
        # 252-column delivery still produced; description slots blank.
        schema = delivery_schema()
        assert result.delivery.column_count == 252
        assert result.delivery.values[column(schema, "MOBILE_DESC")] == ""
        assert result.delivery.values[column(schema, "SHORT_DESC")] == ""
        assert result.delivery.values[column(schema, "Mfg_Part_Num")] == (
            "DCB518ASTS06G"
        )

    def test_description_non_timeout_failure_stays_fatal(self):
        # A non-timeout LLM error (provider unavailable) must still fail the
        # run, proving only timeouts became non-fatal.
        result = make_service(
            provider=acme_provider(),
            records=acme_evidence(),
            llm=FakeLLMClient(
                output=canned_output(),
                error_on_description=LLMProviderUnavailableError(
                    "boom: descriptions provider exploded"
                ),
            ),
        ).run(default_request())

        statuses = {s.stage: s.status for s in result.stages}
        assert statuses[StageName.DESCRIPTION] == StageStatus.FAILED
        assert result.processing.status == ProcessingStatus.FAILED


class TestDeliveryShape:
    def test_no_fabricated_values_without_evidence(self):
        request = default_request(
            E1_Brand="-- Unbranded --",
            Part_Manuf="-",  # official placeholder
            Unilog_Brand="-- No Unilog Brand --",
            DIB_Brand="-- No DIB Brand --",
        )
        result = make_service(provider=FakeProvider([])).run(request)

        schema = delivery_schema()
        values = result.delivery.values
        assert len(values) == 252
        # Passthrough columns keep the verbatim raw cells.
        assert values[column(schema, "Part_Manuf")] == "-"
        assert values[column(schema, "E1_Brand")] == "-- Unbranded --"
        # Ambiguous columns stay blank instead of a fabricated value.
        assert values[column(schema, "BRAND_NAME")] == ""
        assert values[column(schema, "MANUFACTURER_NAME")] == ""
        assert values[column(schema, "MFR URL")] == ""
        # Attribute slots all blank.
        for label, value, uom in schema.attribute_slots():
            assert values[column(schema, label)] == ""
            assert values[column(schema, value)] == ""
        # The missing values are explained, not hidden.
        assert any("BRAND_NAME" in r for r in result.review_reasons)
        assert any("MANUFACTURER_NAME" in r for r in result.review_reasons)

    def test_passthrough_never_dropped_even_with_evidence(self):
        result = success_service().run(default_request())

        schema = delivery_schema()
        assert result.delivery.values[column(schema, "Part_Desc")] == (
            'DCB518ASTS06G Diablo 1/2"x18" - Sanding Belt 6pc'
        )


class TestEnrichApi:
    def test_enrich_endpoint_returns_full_result(self):
        service = success_service()
        app.dependency_overrides[get_enrichment_service] = lambda: service
        try:
            response = TestClient(app).post(
                "/api/enrich", json=default_request().model_dump()
            )
        finally:
            app.dependency_overrides.clear()

        assert response.status_code == 200
        data = response.json()
        assert data["processing"]["status"] == "completed"
        assert data["delivery"]["column_count"] == 252
        assert len(data["delivery"]["values"]) == 252
        assert data["input_row"]["mfg_part_num"] == "DCB518ASTS06G"
        assert data["discovery"]["total_discovered"] == 2
        assert [s["stage"] for s in data["stages"]] == [s.value for s in StageName]
        assert all(s["status"] == "completed" for s in data["stages"])

    def test_enrich_endpoint_rejects_empty_request(self):
        app.dependency_overrides[get_enrichment_service] = lambda: success_service()
        try:
            response = TestClient(app).post(
                "/api/enrich",
                json={
                    "Mfg_Part_Num": "",
                    "Part_Desc": "",
                    "E1_Brand": "",
                    "Unilog_Brand": "",
                    "DIB_Brand": "",
                    "Part_Manuf": "",
                },
            )
        finally:
            app.dependency_overrides.clear()

        assert response.status_code == 422
