"""Regression tests for description structured-output salvage (Step LLM-7).

A partially schema-invalid description response must not destroy all valid
variants: itemized fields (with/application/includes) accept a provider
list[str] that is joined deterministically, malformed fields are blanked
individually with reasons, and unusable whole responses still fail safely.
All tests are offline (FakeLLMClient with canned responses) - no real LLM
API and no network calls are ever made.

TEST FIXTURES: made-up evidence/attribute text used only to exercise
generation logic. These are NOT UniHack data and NOT real manufacturer
data.
"""

import json

import pytest

from app.core.domain import AttributeValue, ProductIdentity
from app.descriptions import DescriptionsService
from app.descriptions.grounding import apply_grounding
from app.llm import LLMInvalidResponseError, LLMProviderUnavailableError
from app.llm.providers.fake import FakeLLMClient


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
        "belt_width": make_attribute("belt_width", "0.5 in", unit="in"),
        "belt_length": make_attribute("belt_length", "18 in", unit="in"),
    }


def service_with(output: str) -> DescriptionsService:
    return DescriptionsService(FakeLLMClient(responses=[output]))


def generate(service: DescriptionsService, **kwargs) -> tuple:
    reasons: list[str] = []
    result = service.generate(
        identity=make_identity(),
        attributes=make_attributes(),
        out_reasons=reasons,
        **kwargs,
    )
    return result, reasons


class TestIncludesField:
    def test_includes_string_preserved_verbatim(self):
        service = service_with(
            json.dumps(
                {"product_title": "Acme Valve", "includes": "6 belts"}
            )
        )
        result, reasons = generate(service)
        assert result.includes == "6 belts"
        assert reasons == []

    def test_includes_list_joined_with_separator(self):
        service = service_with(
            json.dumps(
                {
                    "product_title": "Acme Valve",
                    "includes": ["Push button", "Dust bag"],
                }
            )
        )
        result, reasons = generate(service)
        assert result.includes == "Push button; Dust bag"
        assert result.product_title == "Acme Valve"
        assert reasons == []

    def test_includes_list_blank_items_dropped_order_kept(self):
        service = service_with(
            json.dumps(
                {"includes": ["A", "", "  ", "B"]}
            )
        )
        result, reasons = generate(service)
        assert result.includes == "A; B"
        assert reasons == []


class TestWithField:
    def test_with_list_joined(self):
        service = service_with(
            json.dumps({"with": ["Charger", "Battery"]})
        )
        result, reasons = generate(service)
        assert result.with_ == "Charger; Battery"
        assert reasons == []

    def test_with_string_preserved(self):
        service = service_with(json.dumps({"with": "Charger"}))
        result, reasons = generate(service)
        assert result.with_ == "Charger"


class TestApplicationField:
    def test_application_list_joined(self):
        service = service_with(
            json.dumps({"application": ["Belt sanders", "Angle grinders"]})
        )
        result, reasons = generate(service)
        assert result.application == "Belt sanders; Angle grinders"
        assert reasons == []

    def test_application_string_preserved(self):
        service = service_with(json.dumps({"application": "Belt sanders"}))
        result, reasons = generate(service)
        assert result.application == "Belt sanders"


class TestItemFeatures:
    def test_item_features_list_preserved_in_order(self):
        service = service_with(
            json.dumps({"item_features": ["1/2 inch width", "pack of 6"]})
        )
        result, reasons = generate(service)
        assert result.item_features == ["1/2 inch width", "pack of 6"]
        assert reasons == []

    def test_item_features_non_string_entries_dropped(self):
        service = service_with(
            json.dumps({"item_features": ["width", 42, None, "pack of 6"]})
        )
        result, reasons = generate(service)
        assert result.item_features == ["width", "pack of 6"]
        assert any("non-string" in reason for reason in reasons)

    def test_item_features_non_list_emptied(self):
        service = service_with(json.dumps({"item_features": "not-a-list"}))
        result, reasons = generate(service)
        assert result.item_features == []
        assert any("left blank" in reason for reason in reasons)


class TestMalformedFieldTypes:
    def test_itemized_field_number_blanked(self):
        service = service_with(
            json.dumps(
                {
                    "product_title": "Acme Valve",
                    "includes": 123,
                }
            )
        )
        result, reasons = generate(service)
        assert result.includes == ""
        assert result.product_title == "Acme Valve"
        assert len(reasons) == 1
        assert "includes" in reasons[0]
        assert "left blank" in reasons[0]

    def test_itemized_field_dict_blanked(self):
        service = service_with(json.dumps({"with": {"a": 1}}))
        result, reasons = generate(service)
        assert result.with_ == ""
        assert any("with" in reason for reason in reasons)

    def test_itemized_field_list_with_non_strings_blanked(self):
        service = service_with(
            json.dumps({"includes": ["A", 5, "B"]})
        )
        result, reasons = generate(service)
        assert result.includes == ""
        assert any("includes" in reason for reason in reasons)

    def test_prose_field_boolean_blanked(self):
        service = service_with(
            json.dumps({"product_title": True, "short_description": "ok"})
        )
        result, reasons = generate(service)
        assert result.product_title == ""
        assert result.short_description == "ok"
        assert any("product_title" in reason for reason in reasons)

    def test_prose_field_list_blanked(self):
        service = service_with(
            json.dumps({"product_name": ["Valve", "Regulator"]})
        )
        result, reasons = generate(service)
        assert result.product_name == ""
        assert any("product_name" in reason for reason in reasons)

    def test_none_becomes_empty(self):
        service = service_with(
            json.dumps({"includes": None, "item_features": None})
        )
        result, reasons = generate(service)
        assert result.includes == ""
        assert result.item_features == []


class TestMixedFields:
    def test_mixed_valid_and_malformed_keeps_valid_variants(self):
        service = service_with(
            json.dumps(
                {
                    "product_title": "Acme Belt 6-Pack",
                    "short_description": "Six sanding belts.",
                    "includes": ["Push button", "Dust bag"],
                    "application": 42,
                    "item_features": ["1/2 inch width", None, "pack of 6"],
                    "product_name": ["not", "a", "name"],
                }
            )
        )
        result, reasons = generate(service)
        assert result.product_title == "Acme Belt 6-Pack"
        assert result.short_description == "Six sanding belts."
        assert result.includes == "Push button; Dust bag"
        assert result.application == ""
        assert result.item_features == ["1/2 inch width", "pack of 6"]
        assert result.product_name == ""
        assert any("application" in reason for reason in reasons)
        assert any("product_name" in reason for reason in reasons)


class TestUnsalvageableResponses:
    def test_non_json_response_raises_typed_error(self):
        service = service_with("this is not json")
        with pytest.raises(LLMInvalidResponseError):
            generate(service)

    def test_non_dict_json_response_raises_typed_error(self):
        service = service_with("[1, 2, 3]")
        with pytest.raises(LLMInvalidResponseError):
            generate(service)

    def test_provider_failure_raises_typed_error(self):
        failing = DescriptionsService(
            FakeLLMClient(
                error=LLMProviderUnavailableError("provider down")
            )
        )
        with pytest.raises(LLMProviderUnavailableError):
            generate(failing)

    def test_no_description_fabricated_on_failure(self):
        service = service_with("garbage")
        with pytest.raises(LLMInvalidResponseError):
            generate(service)


class TestNoFabrication:
    def test_join_contains_exactly_provided_items(self):
        service = service_with(
            json.dumps({"includes": ["A", "B", "C"]})
        )
        result, reasons = generate(service)
        assert result.includes == "A; B; C"
        assert result.includes.count(";") == 2
        assert "D" not in result.includes

    def test_unknown_extra_keys_ignored(self):
        service = service_with(
            json.dumps(
                {
                    "product_title": "Acme Valve",
                    "sneaky_field": "not in the schema",
                }
            )
        )
        result, reasons = generate(service)
        assert result.product_title == "Acme Valve"
        assert reasons == []


class TestGroundingStillWorks:
    def test_grounded_salvaged_content_survives_grounding(self):
        service = service_with(
            json.dumps(
                {
                    "product_title": "Acme Belt 6-Pack",
                    "includes": ["Six belts", "Belt sander"],
                }
            )
        )
        result, reasons = generate(
            service,
            quotes=["six belts, 0.5 x 18 in, belt sander"],
        )
        identity = make_identity()
        attributes = make_attributes()
        grounded, ground_reasons, drops = apply_grounding(
            result,
            identity=identity,
            attributes=attributes,
            quotes=["six belts, 0.5 x 18 in, belt sander"],
        )
        assert grounded.includes == "Six belts; Belt sander"
        assert drops == 0
        assert ground_reasons == []

    def test_unsupported_claim_still_blanked_by_grounding(self):
        service = service_with(
            json.dumps(
                {
                    "product_title": "Acme Valve",
                    "includes": ["5-year warranty", "Valve"],
                }
            )
        )
        result, reasons = generate(service)
        grounded, ground_reasons, drops = apply_grounding(
            result,
            identity=make_identity(),
            attributes=make_attributes(),
        )
        assert drops >= 1
        assert grounded.includes == ""
        assert any("grounding" in reason for reason in ground_reasons)