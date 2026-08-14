"""Offline tests for the description generation service (app/descriptions).

The LLM is fully faked: canned JSON responses are fed through the real
LLMClient plumbing (JSON parsing + schema validation), so the tests verify
the service's prompt construction, schema mapping, and failure handling
without any network or provider credentials.
"""

from __future__ import annotations

import json

import pytest

from app.core.domain import AttributeValue, ProductIdentity
from app.descriptions import DescriptionsService
from app.descriptions.service import build_prompt
from app.llm import LLMInvalidResponseError, LLMProviderUnavailableError
from app.llm.providers.fake import FakeLLMClient

GOOD_JSON = json.dumps(
    {
        "product_title": "Acme Belt 6-Pack",
        "short_description": "Six sanding belts.",
        "long_description": "Six sanding belts for belt sanders.",
        "item_features": ["1/2 inch width", "pack of 6"],
        "with": "Six belts",
        "application": "Belt sanders",
        "includes": "6 belts",
    }
)


def make_attribute(
    name: str,
    value: str,
    *,
    unit: str = "",
    confidence: float = 0.9,
) -> AttributeValue:
    return AttributeValue(
        name=name,
        raw_value=value,
        value=value,
        unit=unit,
        confidence=confidence,
        status="validated",
    )


def make_identity() -> ProductIdentity:
    return ProductIdentity(
        manufacturer="Acme Controls",
        mpn="ACME-1000",
        raw_description="ACME-1000 industrial valve",
    )


def make_attributes() -> dict[str, AttributeValue]:
    return {
        "belt_width": make_attribute("belt_width", "0.5 in", unit="in", confidence=0.9),
        "belt_length": make_attribute("belt_length", "18 in", unit="in", confidence=0.85),
    }


def service_with(output: str) -> DescriptionsService:
    return DescriptionsService(FakeLLMClient(responses=[output]))


class TestPrompt:
    def test_prompt_lists_known_attributes_with_units_and_confidence(self):
        prompt = build_prompt(make_identity(), make_attributes(), [])

        assert "ACME-1000" in prompt
        assert "Acme Controls" in prompt
        assert "industrial valve" in prompt
        assert "belt_width: 0.5 in" in prompt
        assert "belt_length: 18 in" in prompt
        assert "(confidence 90%)" in prompt
        assert "use only the facts above" in prompt.lower()

    def test_prompt_includes_evidence_quotes_when_supplied(self):
        prompt = build_prompt(make_identity(), make_attributes(), ["quote-one", "quote-two"])
        assert "quote-one" in prompt
        assert "quote-two" in prompt

    def test_prompt_refuses_empty_attributes(self):
        with pytest.raises(ValueError):
            build_prompt(make_identity(), {}, [])

    def test_low_confidence_slash_unknown_values_not_dropped(self):
        attributes = {
            "belt_length": make_attribute(
                "belt_length", "", confidence=0.1
            )
        }
        prompt = build_prompt(make_identity(), attributes, [])
        # The attribute has no value, so it must not appear as a fact.
        assert "belt_length:" not in prompt


class TestGenerate:
    def test_generates_and_maps_all_variants(self):
        service = service_with(GOOD_JSON)
        result = service.generate(
            identity=make_identity(),
            attributes=make_attributes(),
            quotes=["sanding belts, 0.5 x 18 in, pack of 6"],
        )

        assert result.product_title == "Acme Belt 6-Pack"
        assert result.short_description == "Six sanding belts."
        assert result.long_description == (
            "Six sanding belts for belt sanders."
        )
        assert result.item_features == ["1/2 inch width", "pack of 6"]
        assert result.with_ == "Six belts"
        assert result.application == "Belt sanders"
        assert result.includes == "6 belts"
        assert result.mobile_description == ""
        assert result.invoice_description == ""
        assert result.product_name == ""

    def test_accepts_with_key_from_provider(self):
        # The "with" alias must accept the provider's natural JSON key.
        service = service_with(GOOD_JSON)
        result = service.generate(
            identity=make_identity(), attributes=make_attributes()
        )
        assert result.with_ == "Six belts"

    def test_malformed_provider_output_raises_typed_error(self):
        service = service_with("this is not json")
        with pytest.raises(LLMInvalidResponseError):
            service.generate(
                identity=make_identity(), attributes=make_attributes()
            )

    def test_provider_failure_raises_typed_error(self):
        failing = DescriptionsService(
            FakeLLMClient(
                error=LLMProviderUnavailableError("provider down")
            )
        )
        with pytest.raises(LLMProviderUnavailableError):
            failing.generate(
                identity=make_identity(), attributes=make_attributes()
            )

    def test_empty_variants_stay_empty_never_filled(self):
        service = service_with(json.dumps({}))
        result = service.generate(
            identity=make_identity(), attributes=make_attributes()
        )
        assert result.product_title == ""
        assert result.item_features == []
        assert result.with_ == ""


class TestEvidenceQuotes:
    def test_truncates_and_dedupes_processing(self):
        service = DescriptionsService(FakeLLMClient(responses=["{}"]))
        quotes = service.evidence_quotes(
            ["  lots " * 100, "second document with a real senten ce.", "", "   "],
            limit=2,
        )
        assert len(quotes) == 2
        assert len(quotes[0]) == 280
        assert quotes[1] == "second document with a real senten ce."