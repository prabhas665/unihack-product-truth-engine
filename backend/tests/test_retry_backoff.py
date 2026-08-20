"""Retry/backoff and per-run deadline tests (fully offline)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.config import settings
from app.core.domain import ProductIdentity
from app.llm.base import LLMClient
from app.llm.errors import (
    LLMInvalidResponseError,
    LLMProviderUnavailableError,
)
from app.llm.types import CompletionRequest
from app.pipeline.enrichment import EnrichmentRequest, EnrichmentService
from app.sources import (
    DiscoveryContext,
    DiscoveryResult,
    SourceCandidate,
)
from app.sources.errors import ProviderUnavailableError
from app.utils.retry import retry_call
from app.core.domain import SourceType


class FlakyClient(LLMClient):
    provider = "flaky"

    def __init__(self, failures: int) -> None:
        self._failures = failures
        self.calls = 0

    def _complete(self, prompt, *, system_prompt="", temperature=None, timeout_seconds=None) -> str:
        self.calls += 1
        if self.calls <= self._failures:
            raise LLMProviderUnavailableError(f"{self.provider}: rate limit hit (HTTP 429)")
        return "ok"


class FlakyProvider:
    name = "flaky-provider"

    def __init__(self, failures: int) -> None:
        self._failures = failures
        self.calls = 0
        self._candidate = SourceCandidate(
            url="https://acme-controls.example/products/m1",
            title="M1 Controller",
            source_type=SourceType.MANUFACTURER_PRODUCT_PAGE,
        )

    def discover(self, product, context):
        self.calls += 1
        if self.calls <= self._failures:
            raise ProviderUnavailableError(self.name, "rate limit hit")
        return [self._candidate]


class TestRetryCall:
    def test_succeeds_after_transient_failures(self):
        attempts = {"n": 0}

        def fn():
            attempts["n"] += 1
            if attempts["n"] < 3:
                raise LLMProviderUnavailableError("boom")
            return "done"

        result = retry_call(
            fn,
            attempts=3,
            base_delay=0.0,
            should_retry=lambda exc: isinstance(exc, LLMProviderUnavailableError),
            sleep=lambda _: None,
        )
        assert result == "done"
        assert attempts["n"] == 3

    def test_exhausted_attempts_reraises_last_error(self):
        attempts = {"n": 0}

        def fn():
            attempts["n"] += 1
            raise LLMProviderUnavailableError("always fails")

        with pytest.raises(LLMProviderUnavailableError, match="always fails"):
            retry_call(
                fn,
                attempts=2,
                base_delay=0.0,
                should_retry=lambda exc: isinstance(exc, LLMProviderUnavailableError),
                sleep=lambda _: None,
            )
        assert attempts["n"] == 2

    def test_non_retryable_error_immediate(self):
        attempts = {"n": 0}

        def fn():
            attempts["n"] += 1
            raise LLMInvalidResponseError("schema broken")

        with pytest.raises(LLMInvalidResponseError):
            retry_call(
                fn,
                attempts=5,
                base_delay=0.0,
                should_retry=lambda exc: isinstance(exc, LLMProviderUnavailableError),
                sleep=lambda _: None,
            )
        assert attempts["n"] == 1

    def test_exponential_backoff_delays(self):
        sleeps: list[float] = []
        attempts = {"n": 0}

        def fn():
            attempts["n"] += 1
            if attempts["n"] < 3:
                raise LLMProviderUnavailableError("boom")
            return "done"

        retry_call(
            fn,
            attempts=3,
            base_delay=1.0,
            should_retry=lambda exc: isinstance(exc, LLMProviderUnavailableError),
            sleep=sleeps.append,
        )
        assert sleeps == [1.0, 2.0]


class TestLLMRetry:
    def test_same_provider_retried_on_rate_limit(self, monkeypatch):
        monkeypatch.setattr(settings, "llm_retry_attempts", 3)
        monkeypatch.setattr(settings, "retry_base_delay_seconds", 0.0)
        client = FlakyClient(failures=2)
        result = client.complete(
            CompletionRequest(user_prompt="hello", timeout_seconds=5)
        )
        assert result == "ok"
        assert client.calls == 3

    def test_failure_propagates_after_exhaustion(self, monkeypatch):
        monkeypatch.setattr(settings, "llm_retry_attempts", 2)
        monkeypatch.setattr(settings, "retry_base_delay_seconds", 0.0)
        client = FlakyClient(failures=99)
        with pytest.raises(LLMProviderUnavailableError):
            client.complete(
                CompletionRequest(user_prompt="hello", timeout_seconds=5)
            )
        assert client.calls == 2


class TestDiscoveryRetry:
    def test_provider_retried_on_rate_limit(self, monkeypatch):
        monkeypatch.setattr(settings, "discovery_retry_attempts", 2)
        monkeypatch.setattr(settings, "retry_base_delay_seconds", 0.0)
        provider = FlakyProvider(failures=1)
        product = ProductIdentity(mpn="M1", manufacturer="Acme Controls")
        context = DiscoveryContext(
            product=product, manufacturer_domains=["acme-controls.example"]
        )
        result = run_discovery_with([provider], product, context)
        assert result.total_discovered == 1
        assert result.provider_errors == []
        assert provider.calls == 2

    def test_exhausted_provider_error_recorded(self, monkeypatch):
        monkeypatch.setattr(settings, "discovery_retry_attempts", 1)
        monkeypatch.setattr(settings, "retry_base_delay_seconds", 0.0)
        provider = FlakyProvider(failures=99)
        product = ProductIdentity(mpn="M1", manufacturer="Acme Controls")
        result = run_discovery_with([provider], product)
        assert result.total_discovered == 0
        # One error per pass (exact pass + recall pass), each after its
        # retry attempts were exhausted.
        assert len(result.provider_errors) == 2
        assert provider.calls == 2


def run_discovery_with(providers, product, context=None) -> DiscoveryResult:
    from app.sources.discovery import run_discovery

    return run_discovery(product, providers=providers, context=context)


class TestRunDeadline:
    def test_deadline_skips_extraction_and_descriptions(self, monkeypatch):
        from app.pipeline import enrichment as enrichment_module
        from tests.test_enrichment import (
            FakeLLMClient,
            FakeProvider,
            FakeRetriever,
            canned_output,
            candidate,
            make_service,
        )

        monkeypatch.setattr(settings, "pipeline_run_deadline_seconds", 60)
        clock = {"t": None}

        def fake_utcnow():
            if clock["t"] is None:
                clock["t"] = datetime.now(timezone.utc)
                return clock["t"]
            return clock["t"] + timedelta(seconds=999)

        monkeypatch.setattr(enrichment_module, "utcnow", fake_utcnow)
        service = make_service(
            provider=FakeProvider(candidates=[candidate("https://acme.com/p/1")]),
            manufacturer_domains=["acme.com"],
            retriever=FakeRetriever([]),
            llm=FakeLLMClient(output=canned_output()),
        )
        result = service.run(EnrichmentRequest(Mfg_Part_Num="M1"))
        notes = {state.note for state in result.stages}
        assert any("run deadline exceeded" in note for note in notes)
        assert result.extraction is None
        assert result.processing.status.value != "failed"
        assert result.review_reasons
        assert any("run deadline exceeded" in r for r in result.review_reasons)