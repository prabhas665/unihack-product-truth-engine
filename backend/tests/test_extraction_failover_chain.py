"""Three-model LLM failover tests (Step LLM-8 chain).

The extraction failover chain tries the primary model, then up to two
ordered fallback models, in order, ONLY when the previous attempt times
out or is unavailable (LLMTimeoutError / LLMProviderUnavailableError).
Schema-invalid output never triggers failover: LLM-5 salvage and the
claim-support gate run unchanged on whichever model wins.

All offline: FakeLLMClient (app.llm.providers.fake) and a stubbed
OpenRouterClient factory - no network calls, no real provider credentials.

TEST FIXTURES: made-up evidence/attribute text used only to exercise the
extraction logic, plus the saved real XLC10ZW category-page fixture for
the claim-support regression. These are NOT fabrications of UniHack data.
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
from app.llm import LLMProviderUnavailableError, LLMTimeoutError
from app.llm.providers.fake import FakeLLMClient
from app.pipeline import enrichment as enrichment_module
from app.pipeline.enrichment import StageName, StageStatus
from app.sources.retrieval import EvidenceRecord, RetrievalStatus
from tests.test_extraction_failover import (
    EVIDENCE_A,
    PipelineLLM,
    RecordingClient,
    StubOpenRouterClient,
    default_request,
    extraction_json,
    make_request,
    make_service,
)

PRIMARY_TIMEOUT = LLMTimeoutError("openrouter: wall-clock timeout after 60s")
PRIMARY_429 = LLMProviderUnavailableError(
    "openrouter: rate limit hit (HTTP 429). Retry later or lower the request rate."
)
REJECT_REASON = "claim not found in cited evidence"


class OrderedRecording(FakeLLMClient):
    """Fake that records the order in which attempts happened."""

    def __init__(
        self, label: str, order: list[str], *args, **kwargs
    ) -> None:
        super().__init__(*args, **kwargs)
        self.label = label
        self._order = order

    def _complete(
        self,
        prompt: str,
        *,
        system_prompt: str = "",
        temperature: float | None = None,
        timeout_seconds: float | None = None,
    ) -> str:
        self._order.append(self.label)
        return super()._complete(
            prompt,
            system_prompt=system_prompt,
            temperature=temperature,
            timeout_seconds=timeout_seconds,
        )


def chain_service(
    primary: FakeLLMClient,
    fallback1: FakeLLMClient,
    fallback2: FakeLLMClient,
    *,
    fallback_timeout_seconds: float | None = None,
    fallback_timeout_seconds_2: float | None = None,
) -> ExtractionService:
    return ExtractionService(
        primary,
        fallback_clients=[fallback1, fallback2],
        fallback_timeout_seconds=fallback_timeout_seconds,
        fallback_timeout_seconds_2=fallback_timeout_seconds_2,
    )


def xlc10zw_record() -> EvidenceRecord:
    fixture = json.load(
        open("tests/fixtures/xlc10zw_category_page.json", encoding="utf-8")
    )
    return EvidenceRecord(
        evidence_id=fixture["evidence_id"],
        source_candidate_id="cand-" + fixture["evidence_id"],
        url=fixture["url"],
        source_type=SourceType.MANUFACTURER_PRODUCT_PAGE,
        title=fixture["title"],
        text=fixture["text"],
        retrieval_status=RetrievalStatus.SUCCESS,
    )


def xlc10zw_request() -> ExtractionRequest:
    return ExtractionRequest(
        identity=ProductIdentity(manufacturer="Makita", mpn="XLC10ZW"),
        evidence_records=[xlc10zw_record()],
    )


def probe_items_json() -> str:
    probe = json.load(
        open("tests/fixtures/xlc10zw_probe_attributes.json", encoding="utf-8")
    )
    items = [
        {
            "name": a["name"],
            "raw_value": a["raw_value"],
            "normalized_value": a["normalized_value"],
            "confidence": 1.0,
            "evidence_ids": a["evidence_ids"],
        }
        for a in probe["attributes"]
    ]
    return json.dumps({"items": items})


class TestPrimarySucceeds:
    def test_fallbacks_never_called_when_primary_succeeds(self):
        primary = RecordingClient(responses=[extraction_json()])
        fallback1 = RecordingClient(responses=[extraction_json()])
        fallback2 = RecordingClient(responses=[extraction_json()])
        service = chain_service(primary, fallback1, fallback2)
        response = service.extract(make_request())
        assert [a.name for a in response.attributes] == [
            "belt_width",
            "belt_length",
        ]
        assert len(primary.calls) == 1
        assert fallback1.calls == []
        assert fallback2.calls == []


class TestFallback1Succeeds:
    def test_primary_timeout_fallback1_succeeds_fallback2_not_called(self):
        primary = RecordingClient(error=PRIMARY_TIMEOUT)
        fallback1 = RecordingClient(responses=[extraction_json()])
        fallback2 = RecordingClient(responses=[extraction_json()])
        service = chain_service(primary, fallback1, fallback2)
        response = service.extract(make_request())
        assert [a.name for a in response.attributes] == [
            "belt_width",
            "belt_length",
        ]
        assert response.evidence_ids_used == [EVIDENCE_A]
        assert len(primary.calls) == 1
        assert len(fallback1.calls) == 1
        assert fallback2.calls == []

    def test_primary_429_fallback1_succeeds_fallback2_not_called(self):
        primary = RecordingClient(error=PRIMARY_429)
        fallback1 = RecordingClient(responses=[extraction_json()])
        fallback2 = RecordingClient(responses=[extraction_json()])
        service = chain_service(primary, fallback1, fallback2)
        response = service.extract(make_request())
        assert [a.name for a in response.attributes] == [
            "belt_width",
            "belt_length",
        ]
        assert len(primary.calls) == 1
        assert len(fallback1.calls) == 1
        assert fallback2.calls == []


class TestFallback2Succeeds:
    def test_primary_and_fallback1_timeout_fallback2_succeeds(self):
        primary = RecordingClient(error=PRIMARY_TIMEOUT)
        fallback1 = RecordingClient(error=PRIMARY_TIMEOUT)
        fallback2 = RecordingClient(responses=[extraction_json()])
        service = chain_service(primary, fallback1, fallback2)
        response = service.extract(make_request())
        assert [a.name for a in response.attributes] == [
            "belt_width",
            "belt_length",
        ]
        assert len(primary.calls) == 1
        assert len(fallback1.calls) == 1
        assert len(fallback2.calls) == 1

    def test_primary_and_fallback1_429_fallback2_succeeds(self):
        primary = RecordingClient(error=PRIMARY_TIMEOUT)
        fallback1 = RecordingClient(error=PRIMARY_429)
        fallback2 = RecordingClient(responses=[extraction_json()])
        service = chain_service(primary, fallback1, fallback2)
        response = service.extract(make_request())
        assert [a.name for a in response.attributes] == [
            "belt_width",
            "belt_length",
        ]
        assert len(fallback1.calls) == 1
        assert len(fallback2.calls) == 1


class TestAllThreeFail:
    def test_all_three_timeout_raises_with_last_cause(self):
        primary = RecordingClient(error=PRIMARY_TIMEOUT)
        fallback1 = RecordingClient(error=PRIMARY_TIMEOUT)
        fallback2 = RecordingClient(error=PRIMARY_TIMEOUT)
        service = chain_service(primary, fallback1, fallback2)
        with pytest.raises(ExtractionError) as exc_info:
            service.extract(make_request())
        assert exc_info.value.kind == ExtractionErrorKind.LLM_FAILED
        assert isinstance(exc_info.value.__cause__, LLMTimeoutError)
        assert "primary and all fallback attempts" in exc_info.value.message
        assert len(primary.calls) == 1
        assert len(fallback1.calls) == 1
        assert len(fallback2.calls) == 1

    def test_all_three_unavailable_raises_with_last_cause(self):
        primary = RecordingClient(error=PRIMARY_429)
        fallback1 = RecordingClient(error=PRIMARY_429)
        fallback2 = RecordingClient(error=PRIMARY_429)
        service = chain_service(primary, fallback1, fallback2)
        with pytest.raises(ExtractionError) as exc_info:
            service.extract(make_request())
        assert exc_info.value.kind == ExtractionErrorKind.LLM_FAILED
        assert isinstance(
            exc_info.value.__cause__, LLMProviderUnavailableError
        )
        assert "primary and all fallback attempts" in exc_info.value.message

    def test_no_duplicate_attempts_beyond_the_three_model_chain(self):
        order: list[str] = []
        primary = OrderedRecording("primary", order, error=PRIMARY_TIMEOUT)
        fallback1 = OrderedRecording(
            "fallback1", order, error=PRIMARY_TIMEOUT
        )
        fallback2 = OrderedRecording(
            "fallback2", order, error=PRIMARY_TIMEOUT
        )
        service = chain_service(primary, fallback1, fallback2)
        with pytest.raises(ExtractionError):
            service.extract(make_request())
        assert order == ["primary", "fallback1", "fallback2"]
        assert order.count("primary") == 1
        assert order.count("fallback1") == 1
        assert order.count("fallback2") == 1


class TestNoFallbackOnSchemaInvalid:
    def test_malformed_json_never_triggers_either_fallback(self):
        primary = RecordingClient(responses=["this is not json"])
        fallback1 = RecordingClient(responses=[extraction_json()])
        fallback2 = RecordingClient(responses=[extraction_json()])
        service = chain_service(primary, fallback1, fallback2)
        with pytest.raises(ExtractionError) as exc_info:
            service.extract(make_request())
        assert exc_info.value.kind == ExtractionErrorKind.SCHEMA_INVALID
        assert fallback1.calls == []
        assert fallback2.calls == []

    def test_partial_schema_salvage_never_triggers_either_fallback(self):
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
        fallback1 = RecordingClient(responses=[extraction_json()])
        fallback2 = RecordingClient(responses=[extraction_json()])
        service = chain_service(primary, fallback1, fallback2)
        response = service.extract(make_request())
        # LLM-5 salvage recovered the valid item; the malformed one was
        # rejected locally. No fallback model was ever contacted.
        assert [a.name for a in response.attributes] == ["belt_width"]
        assert len(response.rejected) == 1
        assert fallback1.calls == []
        assert fallback2.calls == []


class TestClaimGateOnFallbackOutput:
    def test_fallback1_output_passes_claim_gate(self):
        # The model behind fallback 1 re-emits the saved XLC10ZW probe
        # claims, including the two that are sibling-contaminated
        # (Speed Settings, Dust Bag Included).
        primary = RecordingClient(error=PRIMARY_TIMEOUT)
        fallback1 = RecordingClient(responses=[probe_items_json()])
        fallback2 = RecordingClient(responses=[extraction_json()])
        service = chain_service(primary, fallback1, fallback2)
        response = service.extract(xlc10zw_request())

        accepted = {a.name for a in response.attributes}
        assert accepted == {
            "Voltage",
            "Motor Type",
            "Power Source",
            "Battery Chemistry",
            "Product Type",
        }
        rejected = {r.name: r.reason for r in response.rejected}
        assert set(rejected) == {"Speed Settings", "Dust Bag Included"}
        assert all(REJECT_REASON in reason for reason in rejected.values())
        assert all(a.quote.strip() for a in response.attributes)
        assert all(
            a.evidence_ids == ["fb15f75b69ed"]
            for a in response.attributes
        )
        assert response.evidence_ids_used == ["fb15f75b69ed"]
        assert len(fallback1.calls) == 1
        assert fallback2.calls == []

    def test_fallback2_output_passes_claim_gate(self):
        # Same workload, but the winning model is fallback 2.
        primary = RecordingClient(error=PRIMARY_TIMEOUT)
        fallback1 = RecordingClient(error=PRIMARY_429)
        fallback2 = RecordingClient(responses=[probe_items_json()])
        service = chain_service(primary, fallback1, fallback2)
        response = service.extract(xlc10zw_request())

        accepted = {a.name for a in response.attributes}
        assert accepted == {
            "Voltage",
            "Motor Type",
            "Power Source",
            "Battery Chemistry",
            "Product Type",
        }
        rejected = {r.name for r in response.rejected}
        assert rejected == {"Speed Settings", "Dust Bag Included"}
        assert all(a.quote.strip() for a in response.attributes)
        assert len(fallback2.calls) == 1


class TestTimeoutBudget:
    def test_attempt_timeouts_recorded_60_30_30(self, monkeypatch):
        monkeypatch.setattr(settings, "llm_timeout_seconds", 60.0)
        primary = RecordingClient(error=PRIMARY_TIMEOUT)
        fallback1 = RecordingClient(error=PRIMARY_TIMEOUT)
        fallback2 = RecordingClient(responses=[extraction_json()])
        service = chain_service(
            primary,
            fallback1,
            fallback2,
            fallback_timeout_seconds=30.0,
            fallback_timeout_seconds_2=30.0,
        )
        service.extract(make_request())
        assert primary.timeouts == [60.0]
        assert fallback1.timeouts == [30.0]
        assert fallback2.timeouts == [30.0]


# --------------------------------------------------------------------------
# pipeline-level: three-model chain wiring + stage mapping
# --------------------------------------------------------------------------


def enable_chain_fallback(
    monkeypatch,
    *,
    first: str = "fallback/model-1:free",
    second: str = "fallback/model-2:free",
) -> None:
    monkeypatch.setattr(settings, "llm_fallback_model", first)
    monkeypatch.setattr(settings, "llm_fallback_model_2", second)
    StubOpenRouterClient.instances.clear()
    monkeypatch.setattr(
        enrichment_module, "OpenRouterClient", StubOpenRouterClient
    )


class TestPipelineThreeModelChain:
    def test_two_fallback_clients_built_in_order(self, monkeypatch):
        monkeypatch.setattr(settings, "llm_fallback_timeout_seconds", 30.0)
        monkeypatch.setattr(settings, "llm_fallback_timeout_seconds_2", None)
        enable_chain_fallback(
            monkeypatch,
            first="openai/gpt-oss-20b:free",
            second="nvidia/nemotron-3.5-lightning:free",
        )
        StubOpenRouterClient.inner = FakeLLMClient(
            error=LLMTimeoutError("fallback timed out")
        )
        result = make_service(
            PipelineLLM(extraction=PRIMARY_TIMEOUT)
        ).run(default_request())

        statuses = {s.stage: s.status for s in result.stages}
        assert statuses[StageName.EXTRACTION] == StageStatus.NEEDS_REVIEW
        assert result.processing.status == ProcessingStatus.NEEDS_REVIEW
        assert result.extraction is None
        assert [c.model for c in StubOpenRouterClient.instances] == [
            "openai/gpt-oss-20b:free",
            "nvidia/nemotron-3.5-lightning:free",
        ]
        # Second fallback timeout is None -> reuses the first fallback's.
        assert [c.timeout_seconds for c in StubOpenRouterClient.instances] == [
            30.0,
            30.0,
        ]
        # Every fallback attempt ran once; no duplicate attempts.
        assert len(StubOpenRouterClient.inner.calls) == 2
        # 252-column delivery still survives with evidence preserved.
        assert result.delivery.column_count == 252
        assert len(result.product.evidence) == 1

    def test_fallback1_success_completes_run(self, monkeypatch):
        enable_chain_fallback(monkeypatch)
        StubOpenRouterClient.inner = FakeLLMClient(
            responses=[extraction_json(EVIDENCE_A)]
        )
        result = make_service(
            PipelineLLM(extraction=PRIMARY_TIMEOUT)
        ).run(default_request())

        statuses = {s.stage: s.status for s in result.stages}
        assert statuses[StageName.EXTRACTION] == StageStatus.COMPLETED
        assert result.processing.status == ProcessingStatus.COMPLETED
        assert result.extraction is not None
        assert len(result.extraction.attributes) == 2
        assert len(StubOpenRouterClient.instances) == 2
        assert len(StubOpenRouterClient.inner.calls) == 1

    def test_chain_disabled_when_both_models_empty(self, monkeypatch):
        monkeypatch.setattr(settings, "llm_fallback_model", "")
        monkeypatch.setattr(settings, "llm_fallback_model_2", "")
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