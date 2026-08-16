"""Failover tests (Step LLM-8): extraction-only fallback model.

Covers the service-level retry (primary timeout/unavailable -> one bounded
fallback attempt, never on schema-invalid output, salvage untouched) and the
pipeline-level stage mapping (both attempts failed -> NEEDS_REVIEW, evidence
preserved, fallback client not built when LLM_FALLBACK_MODEL is empty).

All offline: FakeLLMClient (app.llm.providers.fake) and a stubbed
OpenRouterClient factory - no network calls, no real provider credentials.

TEST FIXTURES: made-up evidence/attribute text used only to exercise the
extraction logic. These are NOT UniHack data and NOT real manufacturer data.
"""

from __future__ import annotations

import json

import pytest

from app.config import settings
from app.core.domain import ProcessingStatus, ProductIdentity, SourceType
from app.extraction import (
    ExtractionError,
    ExtractionErrorKind,
    ExtractionRequest,
    ExtractionService,
)
from app.llm import LLMClient, LLMProviderUnavailableError, LLMTimeoutError
from app.llm.providers.fake import FakeLLMClient
from app.pipeline import enrichment as enrichment_module
from app.pipeline.enrichment import (
    EnrichmentRequest,
    EnrichmentService,
    StageName,
    StageStatus,
)
from app.sources.candidates import DiscoveryMethod, SourceCandidate
from app.sources.retrieval import (
    EvidenceRecord,
    ExtractionStatus,
    RetrievalStatus,
)

EVIDENCE_A = "ev-acme-page-0001"
ACME_PAGE = "https://www.acme.com/products/acme-1000"

DESCRIPTIONS_JSON = json.dumps(
    {
        "product_title": "ACME-1000 Sanding Belt 6-Pack",
        "short_description": "Six-pack of 1/2 x 18 inch sanding belts.",
        "mobile_description": "Acme 1/2x18 in sanding belt, 6 pack.",
        "invoice_description": "Sanding belt 1/2x18 in, pack of 6.",
        "long_description": (
            "A six-pack of Acme sanding belts, each 1/2 inch wide and 18 "
            "inches long, for belt sanders."
        ),
        "retail_description": "Acme 1/2 in x 18 in sanding belt, 6 pack.",
        "marketing_description": "Professional sanding belts in a 6-pack.",
        "item_features": ["1/2 inch width", "18 inch length", "pack of 6"],
        "with": "Six sanding belts",
        "application": "Belt sanders",
        "includes": "6 sanding belts",
        "product_name": "Sanding Belt",
    }
)


def evidence_record(eid: str = EVIDENCE_A) -> EvidenceRecord:
    return EvidenceRecord(
        evidence_id=eid,
        source_candidate_id="cand-1",
        url=ACME_PAGE,
        source_type=SourceType.MANUFACTURER_PRODUCT_PAGE,
        title=ACME_PAGE,
        text="ACME-1000 sanding belt, 1/2 inch x 18 inch, pack of 6.",
        content_type="text/html",
        retrieval_status=RetrievalStatus.SUCCESS,
        extraction_status=ExtractionStatus.EXTRACTED,
    )


def make_request(eids: list[str] | None = None) -> ExtractionRequest:
    eids = eids or [EVIDENCE_A]
    return ExtractionRequest(
        identity=ProductIdentity(
            manufacturer="Acme Controls",
            mpn="ACME-1000",
            raw_description="ACME-1000 sanding belt 6-pack",
        ),
        raw_description="ACME-1000 sanding belt 6-pack",
        evidence_records=[evidence_record(eid) for eid in eids],
    )


def extraction_json(eid: str = EVIDENCE_A) -> str:
    return json.dumps(
        {
            "items": [
                {
                    "name": "belt_width",
                    "raw_value": "0.5 inch",
                    "normalized_value": "0.5 in",
                    "unit": "in",
                    "confidence": 0.9,
                    "evidence_ids": [eid],
                },
                {
                    "name": "belt_length",
                    "raw_value": "18 inch",
                    "normalized_value": "18 in",
                    "unit": "in",
                    "confidence": 0.85,
                    "evidence_ids": [eid],
                },
            ]
        }
    )


class RecordingClient(FakeLLMClient):
    """Fake that also records the timeout_seconds each attempt received."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.timeouts: list[float | None] = []

    def _complete(
        self,
        prompt: str,
        *,
        system_prompt: str = "",
        temperature: float | None = None,
        timeout_seconds: float | None = None,
    ) -> str:
        self.timeouts.append(timeout_seconds)
        return super()._complete(
            prompt,
            system_prompt=system_prompt,
            temperature=temperature,
            timeout_seconds=timeout_seconds,
        )


class TestFailoverDisabled:
    def test_no_fallback_client_behaves_as_before(self):
        primary = FakeLLMClient(responses=[extraction_json()])
        service = ExtractionService(primary)
        response = service.extract(make_request())
        assert [a.name for a in response.attributes] == [
            "belt_width",
            "belt_length",
        ]
        assert response.evidence_ids_used == [EVIDENCE_A]

    def test_timeout_without_fallback_raises_as_before(self):
        primary = FakeLLMClient(
            error=LLMTimeoutError("openrouter: wall-clock timeout after 60s")
        )
        service = ExtractionService(primary)
        with pytest.raises(ExtractionError) as exc_info:
            service.extract(make_request())
        assert exc_info.value.kind == ExtractionErrorKind.LLM_FAILED
        assert "LLM call failed" in exc_info.value.message
        assert isinstance(exc_info.value.__cause__, LLMTimeoutError)

    def test_unavailable_without_fallback_raises_as_before(self):
        primary = FakeLLMClient(error=LLMProviderUnavailableError("boom"))
        service = ExtractionService(primary)
        with pytest.raises(ExtractionError) as exc_info:
            service.extract(make_request())
        assert exc_info.value.kind == ExtractionErrorKind.LLM_FAILED
        assert isinstance(exc_info.value.__cause__, LLMProviderUnavailableError)


class TestFailoverSucceeds:
    def test_primary_timeout_falls_back_to_second_model(self):
        primary = RecordingClient(
            error=LLMTimeoutError("openrouter: wall-clock timeout after 60s")
        )
        fallback = RecordingClient(responses=[extraction_json()])
        service = ExtractionService(primary, fallback_client=fallback)
        response = service.extract(make_request())
        assert [a.name for a in response.attributes] == [
            "belt_width",
            "belt_length",
        ]
        assert response.evidence_ids_used == [EVIDENCE_A]
        assert len(primary.calls) == 1
        assert len(fallback.calls) == 1

    def test_primary_unavailable_falls_back_to_second_model(self):
        primary = RecordingClient(
            error=LLMProviderUnavailableError("boom: provider exploded")
        )
        fallback = RecordingClient(responses=[extraction_json()])
        service = ExtractionService(primary, fallback_client=fallback)
        response = service.extract(make_request())
        assert [a.name for a in response.attributes] == [
            "belt_width",
            "belt_length",
        ]

    def test_primary_success_never_calls_fallback(self):
        primary = RecordingClient(responses=[extraction_json()])
        fallback = RecordingClient(responses=[extraction_json()])
        service = ExtractionService(primary, fallback_client=fallback)
        response = service.extract(make_request())
        assert len(primary.calls) == 1
        assert fallback.calls == []
        assert len(response.attributes) == 2


class TestNoFallbackOnSchemaInvalid:
    def test_malformed_json_never_triggers_fallback(self):
        primary = RecordingClient(responses=["this is not json"])
        fallback = RecordingClient(responses=[extraction_json()])
        service = ExtractionService(primary, fallback_client=fallback)
        with pytest.raises(ExtractionError) as exc_info:
            service.extract(make_request())
        assert exc_info.value.kind == ExtractionErrorKind.SCHEMA_INVALID
        assert fallback.calls == []
        assert fallback.timeouts == []

    def test_salvage_path_never_triggers_fallback(self):
        raw = json.dumps(
            {
                "items": [
                    {
                        "name": "belt_width",
                        "raw_value": "0.5 inch",
                        "normalized_value": "0.5 in",
                        "unit": "in",
                        "confidence": "high",
                        "evidence_ids": [EVIDENCE_A],
                    },
                    {
                        "name": "bogus",
                        "raw_value": "x",
                        "confidence": "not-a-confidence",
                        "evidence_ids": [EVIDENCE_A],
                    },
                ]
            }
        )
        primary = RecordingClient(responses=[raw])
        fallback = RecordingClient(responses=[extraction_json()])
        service = ExtractionService(primary, fallback_client=fallback)
        response = service.extract(make_request())
        # LLM-5 salvage recovered the valid item; the malformed one was
        # rejected locally. The fallback model was never contacted.
        assert [a.name for a in response.attributes] == ["belt_width"]
        assert len(response.rejected) == 1
        assert fallback.calls == []


class TestBothAttemptsFailed:
    def test_fallback_timeout_raises_with_fallback_cause(self):
        primary = RecordingClient(error=LLMTimeoutError("primary timed out"))
        fallback = RecordingClient(error=LLMTimeoutError("fallback timed out"))
        service = ExtractionService(primary, fallback_client=fallback)
        with pytest.raises(ExtractionError) as exc_info:
            service.extract(make_request())
        assert exc_info.value.kind == ExtractionErrorKind.LLM_FAILED
        assert isinstance(exc_info.value.__cause__, LLMTimeoutError)
        assert "both the primary and the fallback" in exc_info.value.message

    def test_fallback_unavailable_raises_with_fallback_cause(self):
        primary = RecordingClient(error=LLMTimeoutError("primary timed out"))
        fallback = RecordingClient(error=LLMProviderUnavailableError("fallback down"))
        service = ExtractionService(primary, fallback_client=fallback)
        with pytest.raises(ExtractionError) as exc_info:
            service.extract(make_request())
        assert exc_info.value.kind == ExtractionErrorKind.LLM_FAILED
        assert isinstance(exc_info.value.__cause__, LLMProviderUnavailableError)


class TestEvidenceSafetyPreserved:
    def test_fallback_path_preserves_evidence_binding(self):
        primary = RecordingClient(error=LLMTimeoutError("primary timed out"))
        fallback = RecordingClient(responses=[extraction_json(EVIDENCE_A)])
        service = ExtractionService(primary, fallback_client=fallback)
        response = service.extract(make_request())
        assert response.evidence_ids_used == [EVIDENCE_A]
        assert all(a.evidence_ids == [EVIDENCE_A] for a in response.attributes)

    def test_fallback_dangling_evidence_still_rejected(self):
        primary = RecordingClient(error=LLMTimeoutError("primary timed out"))
        fallback = RecordingClient(
            responses=[extraction_json("ev-does-not-exist")]
        )
        service = ExtractionService(primary, fallback_client=fallback)
        response = service.extract(make_request())
        assert response.attributes == []
        assert len(response.rejected) == 2
        assert all("dangling" in r.reason for r in response.rejected)


class TestNoFabrication:
    def test_fallback_output_attributes_match_exactly(self):
        primary = RecordingClient(error=LLMTimeoutError("primary timed out"))
        fallback = RecordingClient(responses=[extraction_json()])
        service = ExtractionService(primary, fallback_client=fallback)
        response = service.extract(make_request())
        assert [a.raw_value for a in response.attributes] == [
            "0.5 inch",
            "18 inch",
        ]
        assert [a.unit for a in response.attributes] == ["in", "in"]
        assert len(response.attributes) == 2

    def test_both_failed_returns_nothing(self):
        primary = RecordingClient(error=LLMTimeoutError("primary timed out"))
        fallback = RecordingClient(error=LLMProviderUnavailableError("fallback down"))
        service = ExtractionService(primary, fallback_client=fallback)
        with pytest.raises(ExtractionError):
            service.extract(make_request())


class TestTimeoutBudget:
    def test_each_attempt_gets_its_own_bounded_timeout(self, monkeypatch):
        monkeypatch.setattr(settings, "llm_timeout_seconds", 60.0)
        primary = RecordingClient(error=LLMTimeoutError("primary timed out"))
        fallback = RecordingClient(responses=[extraction_json()])
        service = ExtractionService(
            primary,
            fallback_client=fallback,
            fallback_timeout_seconds=25.0,
        )
        service.extract(make_request())
        # Primary used the global timeout; the fallback used its own.
        assert primary.timeouts == [60.0]
        assert fallback.timeouts == [25.0]


# --------------------------------------------------------------------------
# pipeline-level: stage mapping + fallback client construction
# --------------------------------------------------------------------------


class FakeProvider:
    name = "fake-search"
    kind = DiscoveryMethod.SEARCH

    def __init__(self, candidates: list[SourceCandidate] | None = None) -> None:
        self._candidates = candidates or []

    def discover(self, product, context):
        return list(self._candidates)


class FakeRetriever:
    def __init__(self, records: list[EvidenceRecord]) -> None:
        self.by_url = {record.url: record for record in records}

    def __call__(self, candidate: SourceCandidate) -> EvidenceRecord:
        return self.by_url[candidate.url]


class PipelineLLM(LLMClient):
    """Primary pipeline LLM: fails extraction, serves descriptions."""

    provider = "fake"

    def __init__(
        self,
        *,
        extraction: str | Exception,
        description: str = DESCRIPTIONS_JSON,
    ) -> None:
        self._extraction = extraction
        self._description = description
        self.calls: list[str] = []

    def _complete(
        self,
        prompt: str,
        *,
        system_prompt: str = "",
        temperature: float | None = None,
        timeout_seconds: float | None = None,
    ) -> str:
        self.calls.append(prompt)
        if "PRODUCT IDENTITY" in prompt:
            return self._description
        if isinstance(self._extraction, Exception):
            raise self._extraction
        return self._extraction


class StubOpenRouterClient(LLMClient):
    """Stand-in for app.pipeline.enrichment.OpenRouterClient.

    Records every construction and delegates completions to a settable
    inner FakeLLMClient, so pipeline tests exercise the real wiring without
    network access.
    """

    provider = "openrouter"
    instances: list["StubOpenRouterClient"] = []
    inner: FakeLLMClient | None = None

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        base_url: str,
        timeout_seconds: float,
    ) -> None:
        self.model = model
        self.timeout_seconds = timeout_seconds
        StubOpenRouterClient.instances.append(self)

    def _complete(
        self,
        prompt: str,
        *,
        system_prompt: str = "",
        temperature: float | None = None,
        timeout_seconds: float | None = None,
    ) -> str:
        assert StubOpenRouterClient.inner is not None
        return StubOpenRouterClient.inner._complete(
            prompt,
            system_prompt=system_prompt,
            temperature=temperature,
            timeout_seconds=timeout_seconds,
        )


def candidate(url: str) -> SourceCandidate:
    return SourceCandidate(
        id=f"cand-{url}",
        url=url,
        source_type=SourceType.MANUFACTURER_PRODUCT_PAGE,
        title=url,
    )


def success_record(url: str = ACME_PAGE) -> EvidenceRecord:
    return EvidenceRecord(
        evidence_id=EVIDENCE_A,
        source_candidate_id=f"cand-{url}",
        url=url,
        source_type=SourceType.MANUFACTURER_PRODUCT_PAGE,
        title=url,
        text="ACME-1000 sanding belt, 1/2 inch x 18 inch, pack of 6.",
        content_type="text/html",
        retrieval_status=RetrievalStatus.SUCCESS,
        extraction_status=ExtractionStatus.EXTRACTED,
    )


def default_request(**overrides) -> EnrichmentRequest:
    payload = {
        "Mfg_Part_Num": "ACME-1000",
        "Part_Desc": 'ACME-1000 1/2"x18" sanding belt 6pc',
        "E1_Brand": "-- Unbranded --",
        "Unilog_Brand": "-- No Unilog Brand --",
        "DIB_Brand": "-- No DIB Brand --",
        "Part_Manuf": "Acme Controls",
    }
    payload.update(overrides)
    return EnrichmentRequest(**payload)


def make_service(llm: PipelineLLM) -> EnrichmentService:
    return EnrichmentService(
        providers=[FakeProvider([candidate(ACME_PAGE)])],
        manufacturer_domains=["acme.com"],
        retriever=FakeRetriever([success_record()]),
        llm_client=llm,
    )


def enable_fallback(monkeypatch, *, model: str = "fallback/model:free") -> None:
    monkeypatch.setattr(settings, "llm_fallback_model", model)
    StubOpenRouterClient.instances.clear()
    monkeypatch.setattr(
        enrichment_module, "OpenRouterClient", StubOpenRouterClient
    )


class TestPipelineStageMapping:
    def test_fallback_timeout_maps_to_needs_review(self, monkeypatch):
        enable_fallback(monkeypatch)
        StubOpenRouterClient.inner = FakeLLMClient(
            error=LLMTimeoutError("fallback timed out")
        )
        result = make_service(
            PipelineLLM(extraction=LLMTimeoutError("primary timed out"))
        ).run(default_request())

        statuses = {s.stage: s.status for s in result.stages}
        assert statuses[StageName.EXTRACTION] == StageStatus.NEEDS_REVIEW
        assert result.processing.status == ProcessingStatus.NEEDS_REVIEW
        assert result.extraction is None
        # The fallback model WAS constructed and contacted exactly once.
        assert len(StubOpenRouterClient.instances) == 1
        assert len(StubOpenRouterClient.inner.calls) == 1
        assert any(
            "timed out" in r for r in result.review_reasons
        )

    def test_fallback_unavailable_maps_to_needs_review(self, monkeypatch):
        enable_fallback(monkeypatch)
        StubOpenRouterClient.inner = FakeLLMClient(
            error=LLMProviderUnavailableError("fallback down")
        )
        result = make_service(
            PipelineLLM(extraction=LLMTimeoutError("primary timed out"))
        ).run(default_request())

        statuses = {s.stage: s.status for s in result.stages}
        assert statuses[StageName.EXTRACTION] == StageStatus.NEEDS_REVIEW
        assert result.processing.status == ProcessingStatus.NEEDS_REVIEW
        assert result.extraction is None
        assert len(StubOpenRouterClient.inner.calls) == 1
        # 252-column delivery still survives with evidence preserved.
        assert result.delivery.column_count == 252
        assert len(result.product.evidence) == 1

    def test_fallback_success_completes_run(self, monkeypatch):
        enable_fallback(monkeypatch)
        StubOpenRouterClient.inner = FakeLLMClient(
            responses=[extraction_json(EVIDENCE_A)]
        )
        result = make_service(
            PipelineLLM(extraction=LLMTimeoutError("primary timed out"))
        ).run(default_request())

        statuses = {s.stage: s.status for s in result.stages}
        assert statuses[StageName.EXTRACTION] == StageStatus.COMPLETED
        assert result.extraction is not None
        assert len(result.extraction.attributes) == 2
        assert result.extraction.evidence_ids_used == [EVIDENCE_A]
        assert len(StubOpenRouterClient.inner.calls) == 1

    def test_schema_invalid_with_fallback_enabled_stays_failed(
        self, monkeypatch
    ):
        enable_fallback(monkeypatch)
        StubOpenRouterClient.inner = FakeLLMClient(
            responses=[extraction_json(EVIDENCE_A)]
        )
        result = make_service(
            PipelineLLM(extraction="this is not json")
        ).run(default_request())

        statuses = {s.stage: s.status for s in result.stages}
        assert statuses[StageName.EXTRACTION] == StageStatus.FAILED
        assert result.processing.status == ProcessingStatus.FAILED
        # The fallback model was built but never contacted.
        assert len(StubOpenRouterClient.instances) == 1
        assert StubOpenRouterClient.inner.calls == []

    def test_fallback_client_not_created_when_env_empty(self, monkeypatch):
        # No LLM_FALLBACK_MODEL -> no fallback client is ever constructed
        # and extraction behaves exactly as before.
        monkeypatch.setattr(settings, "llm_fallback_model", "")
        StubOpenRouterClient.instances.clear()
        monkeypatch.setattr(
            enrichment_module, "OpenRouterClient", StubOpenRouterClient
        )
        result = make_service(
            PipelineLLM(extraction=extraction_json(EVIDENCE_A))
        ).run(default_request())

        assert StubOpenRouterClient.instances == []
        statuses = {s.stage: s.status for s in result.stages}
        assert statuses[StageName.EXTRACTION] == StageStatus.COMPLETED
        assert len(result.extraction.attributes) == 2