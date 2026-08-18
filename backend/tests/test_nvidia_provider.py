"""Tests for the NVIDIA NIM LLM provider adapter.

TEST FIXTURES: deterministic, made-up domains/products used ONLY to
exercise the adapter -> structured JSON -> ExtractionService flow. These
are NOT UniHack data and NOT real manufacturer data.

ZERO real API calls: the adapter is driven by httpx.MockTransport with
canned JSON responses. The real NVIDIA NIM API is never contacted (use
backend/.env + the real NVIDIA_NIM_API_KEY for live testing, or
LLM_PROVIDER=fake for offline demos).
"""

import httpx
import pytest

from app.config import settings
from app.core.domain import ProductIdentity, SourceType
from app.extraction import ExtractionRequest as ServiceExtractionRequest
from app.extraction.service import ExtractionService
from app.llm import (
    CompletionRequest,
    LLMClient,
    LLMConfigurationError,
    LLMInvalidResponseError,
    LLMProviderUnavailableError,
    StructuredCompletionRequest,
    get_client,
)
from app.llm.base import PROVIDER_FACTORIES
from app.llm.providers.nvidia import (
    CHAT_COMPLETIONS_PATH,
    DEFAULT_BASE_URL,
    DEFAULT_MODEL,
    NvidiaClient,
)
from app.pipeline import enrichment as enrichment_module
from app.sources.retrieval import EvidenceRecord, RetrievalStatus
from tests.test_extraction_failover import (
    DESCRIPTIONS_JSON,
    PipelineLLM,
    default_request,
    extraction_json,
    make_service,
)

TEST_KEY = "nvapi-test-leak-test-nvidia-000000"

COMPLETION_BODY = {"choices": [{"message": {"content": ""}}]}


def json_completion(content: str, status: int = 200) -> httpx.Response:
    body = dict(COMPLETION_BODY)
    body["choices"] = [{"message": {"content": content}}]
    return httpx.Response(status, json=body)


def mock_client(handler, **overrides) -> NvidiaClient:
    """NvidiaClient pinned to an offline httpx.MockTransport."""
    return NvidiaClient(
        api_key=TEST_KEY,
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        **overrides,
    )


# ------------------------------------------------------------ registration --

class TestRegistration:
    def test_nvidia_provider_is_registered(self):
        assert "nvidia" in PROVIDER_FACTORIES

    def test_llm_provider_nvidia_resolves_to_adapter(self, monkeypatch):
        monkeypatch.setattr(settings, "llm_provider", "nvidia")
        monkeypatch.setattr(settings, "NVIDIA_NIM_API_KEY", TEST_KEY)
        client = get_client()
        assert isinstance(client, NvidiaClient)
        assert client.provider == "nvidia"


# ------------------------------------------------------------------ payload --

class TestRequestPayload:
    def test_default_model_is_nemotron_lightning(self):
        assert DEFAULT_MODEL == "nvidia/nemotron-3.5-lightning-30b-a3b"

    def test_default_base_url_is_nvidia_integrate(self):
        assert DEFAULT_BASE_URL == "https://integrate.api.nvidia.com"

    def test_successful_mocked_completion(self):
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url == f"{DEFAULT_BASE_URL}{CHAT_COMPLETIONS_PATH}"
            body = request.read().decode()
            assert '"role":"user"' in body
            assert '"model":"nvidia/nemotron-3.5-lightning-30b-a3b"' in body
            return json_completion("plain text")

        result = mock_client(handler).complete(CompletionRequest(user_prompt="hi"))
        assert result == "plain text"

    def test_bearer_authorization_header(self):
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.headers["Authorization"] == f"Bearer {TEST_KEY}"
            return json_completion("ok")

        mock_client(handler).complete(CompletionRequest(user_prompt="hi"))

    def test_system_prompt_and_temperature_sent(self):
        def handler(request: httpx.Request) -> httpx.Response:
            body = request.read().decode()
            assert '"role":"system"' in body
            assert '"be careful"' in body
            assert '"temperature":0.2' in body
            return json_completion("ok")

        mock_client(handler).complete(
            CompletionRequest(
                system_prompt="be careful", user_prompt="hi", temperature=0.2
            )
        )

    def test_custom_model_and_base_url_used(self):
        def handler(request: httpx.Request) -> httpx.Response:
            assert (
                request.url
                == "https://mock.nvidia.example/v1/chat/completions"
            )
            assert '"custom-model"' in request.read().decode()
            return json_completion("ok")

        client = mock_client(
            handler,
            model="custom-model",
            base_url="https://mock.nvidia.example",
        )
        client.complete(CompletionRequest(user_prompt="hi"))


# -------------------------------------------------------------- configuration --

class TestConfiguration:
    def test_missing_api_key_raises(self):
        with pytest.raises(LLMConfigurationError):
            NvidiaClient(api_key="")

    def test_from_settings_uses_nvidia_env(self, monkeypatch):
        monkeypatch.setattr(settings, "NVIDIA_NIM_API_KEY", TEST_KEY)
        monkeypatch.setattr(settings, "NVIDIA_MODEL", "")
        client = NvidiaClient.from_settings(settings)
        assert client._model == "nvidia/nemotron-3.5-lightning-30b-a3b"

    def test_from_settings_honors_model_and_base_url(self, monkeypatch):
        monkeypatch.setattr(settings, "NVIDIA_NIM_API_KEY", TEST_KEY)
        monkeypatch.setattr(settings, "NVIDIA_MODEL", "nvidia/custom-model")
        monkeypatch.setattr(settings, "NVIDIA_BASE_URL", "https://mock.nvidia.example")
        client = NvidiaClient.from_settings(settings)
        assert client._model == "nvidia/custom-model"
        assert client._base_url == "https://mock.nvidia.example"

    def test_get_client_nvidia_requires_key(self, monkeypatch):
        from app.llm.base import _clients

        monkeypatch.setattr(settings, "llm_provider", "nvidia")
        monkeypatch.setattr(settings, "NVIDIA_NIM_API_KEY", "")
        monkeypatch.delitem(_clients, "nvidia", raising=False)
        with pytest.raises(LLMConfigurationError):
            get_client()

    def test_repr_never_leaks_api_key(self):
        client = NvidiaClient(api_key=TEST_KEY)
        assert TEST_KEY not in repr(client)
        assert "***" in repr(client)


# ------------------------------------------------------------------- errors --

class TestErrorMapping:
    def test_429_maps_to_provider_unavailable(self):
        handler = lambda request: httpx.Response(429, json={})
        with pytest.raises(LLMProviderUnavailableError) as excinfo:
            mock_client(handler).complete(CompletionRequest(user_prompt="hi"))
        assert "HTTP 429" in str(excinfo.value)

    def test_bad_key_maps_to_provider_unavailable(self):
        handler = lambda request: httpx.Response(
            401, json={"error": {"message": "invalid api key"}}
        )
        with pytest.raises(LLMProviderUnavailableError) as excinfo:
            mock_client(handler).complete(CompletionRequest(user_prompt="hi"))
        assert "authentication failed (HTTP 401)" in str(excinfo.value)
        assert TEST_KEY not in str(excinfo.value)

    def test_missing_choices_maps_to_invalid_response(self):
        handler = lambda request: httpx.Response(200, json={})
        with pytest.raises(LLMInvalidResponseError):
            mock_client(handler).complete(CompletionRequest(user_prompt="hi"))

    def test_empty_content_maps_to_invalid_response(self):
        handler = lambda request: json_completion("")
        with pytest.raises(LLMInvalidResponseError):
            mock_client(handler).complete(CompletionRequest(user_prompt="hi"))


# ---------------------------------------------------- evidence-first extraction --

class TestEvidenceFirstExtraction:
    def make_service(self, handler):
        return ExtractionService(mock_client(handler))

    def test_structured_extraction_through_real_adapter(self):
        evidence = EvidenceRecord(
            evidence_id="ev-1",
            source_candidate_id="cand-1",
            url="https://acme-controls.example/products/m1",
            source_type=SourceType.MANUFACTURER_PRODUCT_PAGE,
            title="M1 Controller",
            text="The M1 controller draws 100 watts and measures 200 mm wide.",
            retrieval_status=RetrievalStatus.SUCCESS,
        )

        def handler(request: httpx.Request) -> httpx.Response:
            sent = request.read().decode()
            assert "M1 controller draws 100 watts" in sent
            return json_completion(
                '{"items": [{"name": "Power", "raw_value": "100 W", '
                '"normalized_value": "100", "unit": "W", "confidence": 0.9, '
                '"evidence_ids": ["ev-1"], "notes": "stated in the datasheet"}]}'
            )

        response = self.make_service(handler).extract(
            ServiceExtractionRequest(
                identity=ProductIdentity(mpn="M1"),
                evidence_records=[evidence],
            )
        )
        assert len(response.attributes) == 1
        assert response.attributes[0].name == "Power"
        assert response.evidence_ids_used == ["ev-1"]

    def test_fenced_json_structured_completion_tolerated(self):
        from pydantic import BaseModel

        class Custom(BaseModel):
            name: str
            count: int

        handler = lambda request: json_completion(
            '```json\n{"name": "motor", "count": 3}\n```'
        )
        result = mock_client(handler).structured_completion(
            StructuredCompletionRequest(user_prompt="structured data", output_schema=Custom)
        )
        assert result.name == "motor"
        assert result.count == 3


# ---------------------------------------------------- nvidia fallback chain wiring --

class StubNvidiaClient(LLMClient):
    """Recording stand-in for enrichment.NvidiaClient (no network)."""

    provider = "nvidia"
    instances: list["StubNvidiaClient"] = []

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
        StubNvidiaClient.instances.append(self)

    def _complete(
        self,
        prompt: str,
        *,
        system_prompt: str = "",
        temperature: float | None = None,
        timeout_seconds: float | None = None,
    ) -> str:
        raise AssertionError("stub must never be called in this test")


class TestNvidiaFallbackChain:
    def enable_nvidia(self, monkeypatch) -> None:
        monkeypatch.setattr(settings, "llm_provider", "nvidia")
        monkeypatch.setattr(settings, "NVIDIA_NIM_API_KEY", TEST_KEY)
        monkeypatch.setattr(settings, "llm_fallback_model", "nvidia/fb-1")
        monkeypatch.setattr(settings, "llm_fallback_model_2", "nvidia/fb-2")
        monkeypatch.setattr(settings, "llm_fallback_timeout_seconds", 30.0)
        monkeypatch.setattr(settings, "llm_fallback_timeout_seconds_2", None)
        StubNvidiaClient.instances.clear()
        monkeypatch.setattr(enrichment_module, "NvidiaClient", StubNvidiaClient)

    def test_fallback_clients_built_from_nvidia_config(self, monkeypatch):
        self.enable_nvidia(monkeypatch)
        result = make_service(
            PipelineLLM(extraction=extraction_json(), description=DESCRIPTIONS_JSON)
        ).run(default_request())

        statuses = {s.stage: s.status for s in result.stages}
        assert statuses[enrichment_module.StageName.DESCRIPTION] == (
            enrichment_module.StageStatus.COMPLETED
        )
        assert result.processing.status.value == "completed"
        # Both stages (extraction + description) build an NVIDIA chain of two.
        assert [c.model for c in StubNvidiaClient.instances] == [
            "nvidia/fb-1",
            "nvidia/fb-2",
            "nvidia/fb-1",
            "nvidia/fb-2",
        ]
        assert all(
            c.timeout_seconds == 30.0 for c in StubNvidiaClient.instances
        )