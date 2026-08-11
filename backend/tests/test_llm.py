"""Unit tests for the LLM provider abstraction (Step 2B).

All tests use the offline FakeLLMClient with canned responses/errors.
No external API is ever called. The registry tests patch settings only.
"""

import pytest
from pydantic import BaseModel, ValidationError

from app.config import settings
from app.core.domain import Classification
from app.llm import (
    ClassificationRequest,
    CompletionRequest,
    DescriptionRequest,
    ExtractionRequest,
    ExtractedAttribute,
    ExtractedAttributes,
    FakeLLMClient,
    LLMConfigurationError,
    LLMError,
    LLMInvalidResponseError,
    LLMProviderUnavailableError,
    LLMTimeoutError,
    StructuredCompletionRequest,
    get_client,
)


class TestExtraction:
    def test_extract_parses_structured_response(self):
        fake = FakeLLMClient(
            responses=[
                '{"items": [{"name": "length", "raw_value": "100 mm", '
                '"unit": "mm", "confidence": 0.9}]}'
            ]
        )
        result = fake.extract(ExtractionRequest(text="The length is 100 mm."))
        assert isinstance(result, ExtractedAttributes)
        assert result.items[0].name == "length"
        assert result.items[0].raw_value == "100 mm"
        assert result.items[0].confidence == 0.9

    def test_extract_builds_default_prompt_from_text(self):
        fake = FakeLLMClient(responses=['{"items": []}'])
        fake.extract(ExtractionRequest(text="some product text"))
        assert "some product text" in fake.calls[0]

    def test_extract_uses_custom_user_prompt_when_given(self):
        fake = FakeLLMClient(responses=['{"items": []}'])
        fake.extract(ExtractionRequest(text="ignored", user_prompt="custom prompt"))
        assert fake.calls[0] == "custom prompt"

    def test_extract_confidence_range_enforced(self):
        with pytest.raises(ValidationError):
            ExtractedAttribute(name="x", raw_value="1", confidence=2.0)


class TestClassification:
    def test_classify_accepts_class_key(self):
        fake = FakeLLMClient(
            responses=['{"department": "Electric", "class": "Motors"}']
        )
        result = fake.classify(ClassificationRequest(text="electric motor"))
        assert isinstance(result, Classification)
        assert result.class_ == "Motors"
        assert result.department == "Electric"


class TestDescription:
    def test_generate_description_returns_text(self):
        fake = FakeLLMClient(responses=['{"text": "A high-quality motor."}'])
        text = fake.generate_description(
            DescriptionRequest(target="short", attributes={"name": "M1"})
        )
        assert text == "A high-quality motor."

    def test_description_prompt_includes_target_and_attributes(self):
        fake = FakeLLMClient(responses=['{"text": "x"}'])
        fake.generate_description(
            DescriptionRequest(target="short", attributes={"length": "100 mm"})
        )
        assert "short" in fake.calls[0]
        assert "100 mm" in fake.calls[0]


class TestStructuredCompletion:
    def test_custom_schema_is_validated(self):
        class Custom(BaseModel):
            name: str
            count: int

        fake = FakeLLMClient(responses=['{"name": "motor", "count": 3}'])
        result = fake.structured_completion(
            StructuredCompletionRequest(
                user_prompt="give me structured data", output_schema=Custom
            )
        )
        assert isinstance(result, Custom)
        assert result.count == 3


class TestErrorHandling:
    def test_malformed_json_raises(self):
        fake = FakeLLMClient(responses=["this is not json at all"])
        with pytest.raises(LLMInvalidResponseError):
            fake.extract(ExtractionRequest(text="x"))

    def test_json_in_markdown_fence_is_accepted(self):
        fake = FakeLLMClient(responses=['```json\n{"items": []}\n```'])
        result = fake.extract(ExtractionRequest(text="x"))
        assert result.items == []

    def test_schema_mismatch_raises(self):
        fake = FakeLLMClient(responses=['{"items": [{"name": "x"}]}'])
        with pytest.raises(LLMInvalidResponseError):
            fake.extract(ExtractionRequest(text="x"))

    def test_provider_typed_error_propagates(self):
        fake = FakeLLMClient(error=LLMTimeoutError("provider timed out"))
        with pytest.raises(LLMTimeoutError):
            fake.complete(CompletionRequest(user_prompt="hi"))

    def test_builtin_timeout_is_mapped(self):
        fake = FakeLLMClient(error=TimeoutError("took too long"))
        with pytest.raises(LLMTimeoutError):
            fake.complete(CompletionRequest(user_prompt="hi"))

    def test_connection_error_is_mapped(self):
        fake = FakeLLMClient(error=ConnectionError("no network"))
        with pytest.raises(LLMProviderUnavailableError):
            fake.complete(CompletionRequest(user_prompt="hi"))

    def test_unexpected_error_is_mapped(self):
        fake = FakeLLMClient(error=RuntimeError("boom"))
        with pytest.raises(LLMProviderUnavailableError):
            fake.complete(CompletionRequest(user_prompt="hi"))

    def test_no_canned_response_is_loud(self):
        fake = FakeLLMClient()
        with pytest.raises(LLMError):
            fake.complete(CompletionRequest(user_prompt="hi"))


class TestProviderRegistry:
    def test_no_provider_configured_raises(self, monkeypatch):
        monkeypatch.setattr(settings, "llm_provider", "")
        with pytest.raises(LLMConfigurationError):
            get_client()

    def test_unknown_provider_raises(self, monkeypatch):
        monkeypatch.setattr(settings, "llm_provider", "does-not-exist")
        with pytest.raises(LLMProviderUnavailableError):
            get_client()

    def test_explicit_provider_argument(self):
        client = get_client(provider="fake")
        assert client.provider == "fake"

    def test_fake_provider_via_env(self, monkeypatch):
        monkeypatch.setattr(settings, "llm_provider", "fake")
        client = get_client()
        assert client.provider == "fake"

    def test_clients_are_cached(self, monkeypatch):
        monkeypatch.setattr(settings, "llm_provider", "fake")
        assert get_client() is get_client()

    def test_fake_provider_works_without_api_key(self):
        """No LLM_API_KEY is set anywhere; fake must still function."""
        client = get_client(provider="fake")
        assert settings.llm_api_key == ""
        assert client.provider == "fake"
