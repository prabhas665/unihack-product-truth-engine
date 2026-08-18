"""Tests for the real DeepSeek LLM provider adapter (Step 6C).

TEST FIXTURES: deterministic, made-up domains/products used ONLY to exercise
the adapter -> structured JSON -> ExtractionService flow. These are NOT
UniHack data and NOT real manufacturer data.

ZERO real API calls: the adapter is driven by httpx.MockTransport with
canned JSON responses. The real DeepSeek API is never contacted (use
`.env` + a real key for live testing, or LLM_PROVIDER=fake for offline
demos).
"""

import httpx
import pytest

from app.config import settings
from app.core.domain import ProductIdentity, SourceType
from app.extraction import ExtractionRequest as ServiceExtractionRequest
from app.extraction.service import ExtractionService
from app.llm import (
    ClassificationRequest,
    CompletionRequest,
    DescriptionRequest,
    DeepSeekClient,
    ExtractionRequest,
    ExtractedAttributes,
    LLMConfigurationError,
    LLMInvalidResponseError,
    LLMProviderUnavailableError,
    LLMTimeoutError,
    StructuredCompletionRequest,
    get_client,
)
from app.sources.retrieval import EvidenceRecord, RetrievalStatus

TEST_KEY = "sk-leak-test-6c-1234567890"

COMPLETION_BODY = {
    "id": "chatcmpl-test",
    "choices": [{"message": {"role": "assistant", "content": ""}}],
}


def json_completion(content: str, status: int = 200) -> httpx.Response:
    body = dict(COMPLETION_BODY)
    body["choices"] = [
        {"message": {"role": "assistant", "content": content}}
    ]
    return httpx.Response(status, json=body)


def mock_client(handler, **overrides) -> DeepSeekClient:
    """DeepSeekClient pinned to an offline httpx.MockTransport."""
    return DeepSeekClient(
        api_key=TEST_KEY,
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        **overrides,
    )


# ------------------------------------------------------------------- payload --

class TestRequestPayload:
    def test_default_model_and_endpoint(self):
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url == "https://api.deepseek.com/chat/completions"
            body = request.read().decode()
            assert '"model":"deepseek-chat"' in body
            assert '"role":"user"' in body
            assert '"role":"system"' not in body  # empty system prompt omitted
            return json_completion("plain text")

        result = mock_client(handler).complete(CompletionRequest(user_prompt="hi"))
        assert result == "plain text"

    def test_authorization_bearer_header(self):
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.headers["Authorization"] == f"Bearer {TEST_KEY}"
            return json_completion("ok")

        mock_client(handler).complete(CompletionRequest(user_prompt="hi"))

    def test_system_prompt_and_temperature_sent(self):
        def handler(request: httpx.Request) -> httpx.Response:
            body = request.read().decode()
            assert '"role":"system"' in body
            assert '"content":"be careful"' in body
            assert '"temperature":0.2' in body
            return json_completion("ok")

        mock_client(handler).complete(
            CompletionRequest(
                user_prompt="hi", system_prompt="be careful", temperature=0.2
            )
        )

    def test_model_and_base_url_overrides(self):
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url == "https://mock.llm.example/v1/chat/completions"
            assert '"model":"deepseek-reasoner"' in request.read().decode()
            return json_completion("ok")

        mock_client(
            handler, model="deepseek-reasoner", base_url="https://mock.llm.example/v1"
        ).complete(CompletionRequest(user_prompt="hi"))


# ------------------------------------------------------------ typed failures --

class TestFailureMapping:
    def test_auth_failure_401(self):
        handler = lambda request: httpx.Response(401, json={"error": "invalid key"})
        with pytest.raises(LLMProviderUnavailableError) as excinfo:
            mock_client(handler).complete(CompletionRequest(user_prompt="hi"))
        assert "401" in str(excinfo.value)
        assert TEST_KEY not in str(excinfo.value)

    def test_auth_failure_403(self):
        handler = lambda request: httpx.Response(403, json={"error": "denied"})
        with pytest.raises(LLMProviderUnavailableError, match="authentication"):
            mock_client(handler).complete(CompletionRequest(user_prompt="hi"))

    def test_rate_limit_429(self):
        handler = lambda request: httpx.Response(429, json={"error": "slow down"})
        with pytest.raises(LLMProviderUnavailableError, match="rate limit"):
            mock_client(handler).complete(CompletionRequest(user_prompt="hi"))

    def test_server_error_500(self):
        handler = lambda request: httpx.Response(500, json={"error": "boom"})
        with pytest.raises(LLMProviderUnavailableError, match="HTTP 500"):
            mock_client(handler).complete(CompletionRequest(user_prompt="hi"))

    def test_timeout_maps_to_llm_timeout(self):
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectTimeout("slow provider")

        with pytest.raises(LLMTimeoutError):
            mock_client(handler).complete(CompletionRequest(user_prompt="hi"))

    def test_connection_error_maps_to_unavailable(self):
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused")

        with pytest.raises(LLMProviderUnavailableError, match="unreachable"):
            mock_client(handler).complete(CompletionRequest(user_prompt="hi"))

    def test_malformed_json_maps_to_invalid_response(self):
        handler = lambda request: httpx.Response(200, content=b"<html>")
        with pytest.raises(LLMInvalidResponseError):
            mock_client(handler).extract(ExtractionRequest(text="x"))

    def test_missing_choices_maps_to_invalid_response(self):
        handler = lambda request: httpx.Response(200, json={"id": "x"})
        with pytest.raises(LLMInvalidResponseError, match="choices"):
            mock_client(handler).complete(CompletionRequest(user_prompt="hi"))

    def test_empty_content_maps_to_invalid_response(self):
        handler = lambda request: httpx.Response(200, json=COMPLETION_BODY)
        with pytest.raises(LLMInvalidResponseError, match="empty message content"):
            mock_client(handler).complete(CompletionRequest(user_prompt="hi"))

    def test_schema_invalid_content_maps_to_invalid_response(self):
        handler = lambda request: json_completion('{"items": [{"name": "x"}]}')
        with pytest.raises(LLMInvalidResponseError):
            mock_client(handler).extract(ExtractionRequest(text="x"))

    def test_markdown_fenced_json_accepted(self):
        handler = lambda request: json_completion(
            '```json\n{"items": [{"name": "length", "raw_value": "100 mm"}]\n}\n```'
        )
        result = mock_client(handler).extract(ExtractionRequest(text="x"))
        assert result.items[0].name == "length"


# ---------------------------------------------------------------- configuration --

class TestConfiguration:
    def test_missing_api_key_raises_typed_error(self):
        with pytest.raises(LLMConfigurationError, match="LLM_API_KEY"):
            DeepSeekClient(api_key="")

    def test_from_settings_missing_key(self, monkeypatch):
        monkeypatch.setattr(settings, "llm_api_key", "")
        with pytest.raises(LLMConfigurationError, match="LLM_API_KEY"):
            DeepSeekClient.from_settings(settings)

    def test_from_settings_model_falls_back_to_default(self, monkeypatch):
        monkeypatch.setattr(settings, "llm_api_key", TEST_KEY)
        monkeypatch.setattr(settings, "llm_model", "")
        client = DeepSeekClient.from_settings(settings)
        assert client._model == "deepseek-chat"

    def test_from_settings_honors_model_and_base_url(self, monkeypatch):
        monkeypatch.setattr(settings, "llm_api_key", TEST_KEY)
        monkeypatch.setattr(settings, "llm_model", "deepseek-reasoner")
        monkeypatch.setattr(settings, "llm_base_url", "https://mock.llm.example")
        client = DeepSeekClient.from_settings(settings)
        assert client._model == "deepseek-reasoner"
        assert client._base_url == "https://mock.llm.example"

    def test_app_starts_without_provider_configured(self, monkeypatch):
        monkeypatch.setattr(settings, "llm_provider", "")
        monkeypatch.setattr(settings, "llm_api_key", "")
        with pytest.raises(LLMConfigurationError):
            get_client()

    def test_get_client_deepseek_requires_key(self, monkeypatch):
        monkeypatch.setattr(settings, "llm_provider", "deepseek")
        monkeypatch.setattr(settings, "llm_api_key", "")
        with pytest.raises(LLMConfigurationError):
            get_client()

    def test_get_client_returns_deepseek_adapter(self, monkeypatch):
        monkeypatch.setattr(settings, "llm_provider", "deepseek")
        monkeypatch.setattr(settings, "llm_api_key", TEST_KEY)
        client = get_client()
        assert isinstance(client, DeepSeekClient)

    def test_repr_never_leaks_api_key(self):
        client = DeepSeekClient(api_key=TEST_KEY)
        assert TEST_KEY not in repr(client)
        assert "***" in repr(client)


# ---------------------------------------------------- evidence-first extraction --

class TestEvidenceFirstExtraction:
    def make_service(self, handler):
        return ExtractionService(mock_client(handler))

    def test_evidence_ids_preserved_through_real_adapter(self):
        evidence = EvidenceRecord(
            evidence_id="ev-1",
            candidate_id="cand-1",
            url="https://acme-controls.example/products/m1",
            source_type=SourceType.MANUFACTURER_PRODUCT_PAGE,
            title="M1 Controller",
            text="The M1 controller draws 100 W and measures 200 mm wide.",
            status=RetrievalStatus.SUCCESS,
        )

        def handler(request: httpx.Request) -> httpx.Response:
            sent = request.read().decode()
            assert "M1 controller draws 100 W" in sent  # evidence reaches LLM
            assert "ev-1" in sent
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
        assert response.attributes[0].evidence_ids == ["ev-1"]
        assert response.evidence_ids_used == ["ev-1"]

    def test_claim_without_evidence_rejected(self):
        evidence = EvidenceRecord(
            evidence_id="ev-1",
            candidate_id="cand-1",
            url="https://acme-controls.example/products/m1",
            source_type=SourceType.MANUFACTURER_PRODUCT_PAGE,
            title="M1 Controller",
            text="The M1 controller draws 100 watts.",
            status=RetrievalStatus.SUCCESS,
        )

        def handler(request: httpx.Request) -> httpx.Response:
            return json_completion(
                '{"items": [{"name": "Power", "raw_value": "999 W", '
                '"confidence": 0.9, "evidence_ids": []}]}'
            )

        response = self.make_service(handler).extract(
            ServiceExtractionRequest(
                identity=ProductIdentity(mpn="M1"),
                evidence_records=[evidence],
            )
        )
        assert response.attributes == []
        assert len(response.rejected) == 1
        assert "no evidence_ids" in response.rejected[0].reason

    def test_dangling_evidence_id_rejected(self):
        evidence = EvidenceRecord(
            evidence_id="ev-1",
            candidate_id="cand-1",
            url="https://acme-controls.example/products/m1",
            source_type=SourceType.MANUFACTURER_PRODUCT_PAGE,
            title="M1 Controller",
            text="The M1 controller draws 100 watts.",
            status=RetrievalStatus.SUCCESS,
        )

        def handler(request: httpx.Request) -> httpx.Response:
            return json_completion(
                '{"items": [{"name": "Power", "raw_value": "100 W", '
                '"confidence": 0.9, "evidence_ids": ["not-supplied"]}]}'
            )

        response = self.make_service(handler).extract(
            ServiceExtractionRequest(
                identity=ProductIdentity(mpn="M1"),
                evidence_records=[evidence],
            )
        )
        assert response.attributes == []
        assert "dangling evidence id" in response.rejected[0].reason

    def test_provider_auth_failure_raises_typed_extraction_error(self):
        evidence = EvidenceRecord(
            evidence_id="ev-1",
            candidate_id="cand-1",
            url="https://acme-controls.example/products/m1",
            source_type=SourceType.MANUFACTURER_PRODUCT_PAGE,
            title="M1 Controller",
            text="The M1 controller draws 100 watts.",
            status=RetrievalStatus.SUCCESS,
        )
        handler = lambda request: httpx.Response(401, json={"error": "bad key"})
        from app.extraction import ExtractionError, ExtractionErrorKind

        with pytest.raises(ExtractionError) as excinfo:
            self.make_service(handler).extract(
                ServiceExtractionRequest(
                    identity=ProductIdentity(mpn="M1"),
                    evidence_records=[evidence],
                )
            )
        assert excinfo.value.kind == ExtractionErrorKind.LLM_FAILED
        assert TEST_KEY not in str(excinfo.value)


# ------------------------------------------------------- other operations through adapter --

class TestOtherOperationsThroughAdapter:
    def test_classify_through_real_adapter(self):
        handler = lambda request: json_completion(
            '{"department": "Electric", "class": "Motors"}'
        )
        result = mock_client(handler).classify(
            ClassificationRequest(text="electric motor")
        )
        assert result.department == "Electric"
        assert result.class_ == "Motors"

    def test_generate_description_through_real_adapter(self):
        handler = lambda request: json_completion('{"text": "A reliable motor."}')
        text = mock_client(handler).generate_description(
            DescriptionRequest(target="short", attributes={"name": "M1"})
        )
        assert text == "A reliable motor."

    def test_structured_completion_through_real_adapter(self):
        from pydantic import BaseModel

        class Custom(BaseModel):
            name: str
            count: int

        handler = lambda request: json_completion('{"name": "motor", "count": 3}')
        result = mock_client(handler).structured_completion(
            StructuredCompletionRequest(
                user_prompt="structured data", output_schema=Custom
            )
        )
        assert isinstance(result, Custom)
        assert result.count == 3
