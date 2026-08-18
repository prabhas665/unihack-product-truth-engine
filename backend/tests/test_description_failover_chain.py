"""Three-model description failover tests.

The description generation step retries the ordered fallback models
(LLM_FALLBACK_MODEL, then LLM_FALLBACK_MODEL_2) ONLY when the previous
attempt times out or is unavailable (LLMTimeoutError /
LLMProviderUnavailableError) - the same rule as extraction. Schema-invalid
output never triggers a failover and stays a FAILED stage. When every
attempt fails the stage becomes NEEDS_REVIEW with blank description
fields: the run, evidence and 252-column delivery survive and nothing is
fabricated.

All offline: FakeLLMClient and the stubbed OpenRouterClient factory from
the extraction failover suite - no network calls, no real credentials.

TEST FIXTURES: made-up evidence/attribute text used only to exercise the
description logic. These are NOT UniHack data and NOT real manufacturer data.
"""

from __future__ import annotations

from app.config import settings
from app.core.domain import ProcessingStatus
from app.llm import LLMError
from app.llm.providers.fake import FakeLLMClient
from app.pipeline import enrichment as enrichment_module
from app.pipeline.enrichment import StageName, StageStatus
from tests.test_extraction_failover import (
    DESCRIPTIONS_JSON,
    PipelineLLM,
    StubOpenRouterClient,
    default_request,
    extraction_json,
    make_service,
)
from tests.test_extraction_failover_chain import (
    PRIMARY_429,
    PRIMARY_TIMEOUT,
    enable_chain_fallback,
)

DESCRIPTION_TIMEOUT_REASON = (
    "Description generation unavailable: OpenRouter timeout."
)

# Extraction also builds two fallback clients (it runs first); the
# description chain is always the last two constructed instances.
DESCRIPTION_INSTANCES = slice(-2, None)


class ScriptedClient(FakeLLMClient):
    """Fake that replays a script of responses or raised errors per call."""

    def __init__(self, script: list) -> None:
        super().__init__()
        self._script = list(script)

    def _complete(
        self,
        prompt: str,
        *,
        system_prompt: str = "",
        temperature: float | None = None,
        timeout_seconds: float | None = None,
    ) -> str:
        self.calls.append(prompt)
        if not self._script:
            raise LLMError("script exhausted")
        step = self._script.pop(0)
        if isinstance(step, Exception):
            raise step
        return step


class DescriptionPipelineLLM(PipelineLLM):
    """Pipeline primary LLM that RAISES the description error (PipelineLLM
    itself returns it as a string, which is fine for its own tests)."""

    def _complete(
        self,
        prompt: str,
        *,
        system_prompt: str = "",
        temperature: float | None = None,
        timeout_seconds: float | None = None,
    ) -> str:
        if "PRODUCT IDENTITY" in prompt:
            if isinstance(self._description, Exception):
                raise self._description
            return self._description
        return super()._complete(
            prompt,
            system_prompt=system_prompt,
            temperature=temperature,
            timeout_seconds=timeout_seconds,
        )


def description_service(description: str | Exception):
    return make_service(
        DescriptionPipelineLLM(
            extraction=extraction_json(), description=description
        )
    )


def run_description(description: str | Exception):
    return description_service(description).run(default_request())


class TestPerAttemptTimeout:
    def test_generate_forwards_per_attempt_timeout(self):
        """Each fallback attempt gets its own wall-clock timeout, not the
        primary's (NVIDIA is slow and needs the longer bound)."""
        from app.core.domain import AttributeValue, ProductIdentity
        from app.descriptions.service import DescriptionsService
        from app.llm import LLMClient

        captured: dict = {}

        class TimeoutProbe(LLMClient):
            provider = "fake"

            def _complete(
                self,
                prompt: str,
                *,
                system_prompt: str = "",
                temperature: float | None = None,
                timeout_seconds: float | None = None,
            ) -> str:
                return DESCRIPTIONS_JSON

            def structured_completion(self, request):
                captured["timeout"] = request.timeout_seconds
                return super().structured_completion(request)

        service = DescriptionsService(TimeoutProbe())
        service.generate(
            identity=ProductIdentity(mpn="ACME-1000"),
            attributes={
                "Material": AttributeValue(
                    name="Material",
                    raw_value="aluminum",
                    value="aluminum",
                )
            },
            timeout_seconds=123.0,
        )
        assert captured["timeout"] == 123.0


class TestPrimarySucceeds:
    def test_fallbacks_built_but_never_called(self, monkeypatch):
        enable_chain_fallback(monkeypatch)
        StubOpenRouterClient.inner = ScriptedClient([DESCRIPTIONS_JSON])
        result = run_description(DESCRIPTIONS_JSON)

        statuses = {s.stage: s.status for s in result.stages}
        assert statuses[StageName.DESCRIPTION] == StageStatus.COMPLETED
        assert result.processing.status == ProcessingStatus.COMPLETED
        assert result.product is not None
        assert (
            result.product.descriptions.product_title
            == "ACME-1000 Sanding Belt 6-Pack"
        )
        assert [c.model for c in StubOpenRouterClient.instances[DESCRIPTION_INSTANCES]] == [
            "fallback/model-1:free",
            "fallback/model-2:free",
        ]
        assert StubOpenRouterClient.inner.calls == []


class TestFallback1Succeeds:
    def test_primary_timeout_fallback1_succeeds(self, monkeypatch):
        enable_chain_fallback(monkeypatch)
        StubOpenRouterClient.inner = ScriptedClient([DESCRIPTIONS_JSON])
        result = run_description(PRIMARY_TIMEOUT)

        statuses = {s.stage: s.status for s in result.stages}
        assert statuses[StageName.DESCRIPTION] == StageStatus.COMPLETED
        assert result.processing.status == ProcessingStatus.COMPLETED
        assert (
            result.product.descriptions.product_title
            == "ACME-1000 Sanding Belt 6-Pack"
        )
        assert len(StubOpenRouterClient.inner.calls) == 1

    def test_primary_429_fallback1_succeeds(self, monkeypatch):
        enable_chain_fallback(monkeypatch)
        StubOpenRouterClient.inner = ScriptedClient([DESCRIPTIONS_JSON])
        result = run_description(PRIMARY_429)

        statuses = {s.stage: s.status for s in result.stages}
        assert statuses[StageName.DESCRIPTION] == StageStatus.COMPLETED
        assert result.processing.status == ProcessingStatus.COMPLETED
        assert len(StubOpenRouterClient.inner.calls) == 1


class TestFallback2Succeeds:
    def test_primary_and_fallback1_fail_fallback2_succeeds(self, monkeypatch):
        enable_chain_fallback(monkeypatch)
        StubOpenRouterClient.inner = ScriptedClient(
            [PRIMARY_TIMEOUT, DESCRIPTIONS_JSON]
        )
        result = run_description(PRIMARY_429)

        statuses = {s.stage: s.status for s in result.stages}
        assert statuses[StageName.DESCRIPTION] == StageStatus.COMPLETED
        assert result.processing.status == ProcessingStatus.COMPLETED
        assert (
            result.product.descriptions.product_title
            == "ACME-1000 Sanding Belt 6-Pack"
        )
        assert len(StubOpenRouterClient.inner.calls) == 2


class TestAllThreeFail:
    def test_all_three_429_stage_needs_review_run_survives(self, monkeypatch):
        enable_chain_fallback(monkeypatch)
        StubOpenRouterClient.inner = ScriptedClient([PRIMARY_429, PRIMARY_429])
        result = run_description(PRIMARY_429)

        statuses = {s.stage: s.status for s in result.stages}
        assert statuses[StageName.DESCRIPTION] == StageStatus.NEEDS_REVIEW
        assert result.processing.status == ProcessingStatus.NEEDS_REVIEW
        assert result.processing.status != ProcessingStatus.FAILED
        # Blank description fields: nothing is fabricated.
        assert result.product is not None
        assert result.product.descriptions.product_title == ""
        assert result.product.descriptions.short_description == ""
        # The reason records every attempt with its position.
        detailed = [
            r
            for r in result.review_reasons
            if "description generation failed on the primary and all "
            "fallback attempts" in r
        ]
        assert len(detailed) == 1
        assert "0: " in detailed[0] and "2: " in detailed[0]
        # Exactly one attempt per model; no duplicates beyond the chain.
        assert len(StubOpenRouterClient.inner.calls) == 2
        # 252-column delivery still produced with evidence preserved.
        assert result.delivery.column_count == 252
        assert len(result.product.evidence) == 1

    def test_all_three_timeout_appends_timeout_reason(self, monkeypatch):
        enable_chain_fallback(monkeypatch)
        StubOpenRouterClient.inner = ScriptedClient(
            [PRIMARY_TIMEOUT, PRIMARY_TIMEOUT]
        )
        result = run_description(PRIMARY_TIMEOUT)

        statuses = {s.stage: s.status for s in result.stages}
        assert statuses[StageName.DESCRIPTION] == StageStatus.NEEDS_REVIEW
        assert result.processing.status == ProcessingStatus.NEEDS_REVIEW
        assert DESCRIPTION_TIMEOUT_REASON in result.review_reasons
        assert len(StubOpenRouterClient.inner.calls) == 2


class TestNoFallbackOnSchemaInvalid:
    def test_malformed_output_never_triggers_either_fallback(self, monkeypatch):
        enable_chain_fallback(monkeypatch)
        StubOpenRouterClient.inner = ScriptedClient([DESCRIPTIONS_JSON])
        result = run_description("this is not json")

        statuses = {s.stage: s.status for s in result.stages}
        assert statuses[StageName.DESCRIPTION] == StageStatus.FAILED
        assert result.processing.status == ProcessingStatus.FAILED
        assert StubOpenRouterClient.inner.calls == []
        assert any(
            "description generation failed" in r for r in result.review_reasons
        )


class TestChainDisabled:
    def test_timeout_keeps_single_client_behavior(self, monkeypatch):
        monkeypatch.setattr(settings, "llm_fallback_model", "")
        monkeypatch.setattr(settings, "llm_fallback_model_2", "")
        StubOpenRouterClient.instances.clear()
        monkeypatch.setattr(
            enrichment_module, "OpenRouterClient", StubOpenRouterClient
        )
        result = run_description(PRIMARY_TIMEOUT)

        statuses = {s.stage: s.status for s in result.stages}
        assert statuses[StageName.DESCRIPTION] == StageStatus.NEEDS_REVIEW
        assert result.processing.status == ProcessingStatus.NEEDS_REVIEW
        assert DESCRIPTION_TIMEOUT_REASON in result.review_reasons
        assert StubOpenRouterClient.instances == []


class TestTimeoutBudget:
    def test_fallback_clients_carry_30_30_timeouts(self, monkeypatch):
        monkeypatch.setattr(settings, "llm_fallback_timeout_seconds", 30.0)
        monkeypatch.setattr(settings, "llm_fallback_timeout_seconds_2", 30.0)
        enable_chain_fallback(monkeypatch)
        StubOpenRouterClient.inner = ScriptedClient([DESCRIPTIONS_JSON])
        run_description(PRIMARY_TIMEOUT)

        assert [
            c.timeout_seconds
            for c in StubOpenRouterClient.instances[DESCRIPTION_INSTANCES]
        ] == [30.0, 30.0]

    def test_fallback2_timeout_reuses_fallback1_timeout(self, monkeypatch):
        monkeypatch.setattr(settings, "llm_fallback_timeout_seconds", 30.0)
        monkeypatch.setattr(settings, "llm_fallback_timeout_seconds_2", None)
        enable_chain_fallback(monkeypatch)
        StubOpenRouterClient.inner = ScriptedClient([DESCRIPTIONS_JSON])
        run_description(PRIMARY_TIMEOUT)

        assert [
            c.timeout_seconds
            for c in StubOpenRouterClient.instances[DESCRIPTION_INSTANCES]
        ] == [30.0, 30.0]