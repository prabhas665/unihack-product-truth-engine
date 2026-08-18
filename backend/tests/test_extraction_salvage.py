"""Regression tests for structured-response salvage (Step LLM-5).

A partially schema-invalid structured response must not invalidate valid
attributes: confidence "high"/"medium"/"low" is normalized deterministically,
unknown/out-of-range/bool confidence rejects only that item, and evidence
binding stays strict. All tests are offline (FakeLLMClient with canned
responses) - no real LLM API and no network calls are ever made.

TEST FIXTURES: made-up evidence text used only to exercise extraction logic.
These are NOT UniHack data and NOT real manufacturer data.
"""

import pytest

from app.core.domain import ProductIdentity, SourceType
from app.extraction import (
    ExtractionError,
    ExtractionErrorKind,
    ExtractionRequest,
    ExtractionResponse,
    ExtractionService,
)
from app.llm import FakeLLMClient
from app.sources.retrieval import EvidenceRecord, RetrievalStatus


def make_evidence(
    evidence_id: str, text: str, title: str = "", url: str = ""
) -> EvidenceRecord:
    return EvidenceRecord(
        evidence_id=evidence_id,
        source_candidate_id="cand-" + evidence_id,
        url=url or f"https://acme-controls.example/{evidence_id}",
        source_type=SourceType.MANUFACTURER_PRODUCT_PAGE,
        title=title or "Fixture page",
        text=text,
        retrieval_status=RetrievalStatus.SUCCESS,
    )


def make_request(*records: EvidenceRecord) -> ExtractionRequest:
    return ExtractionRequest(
        identity=ProductIdentity(
            manufacturer="Acme Controls", brand="Acme", mpn="M1"
        ),
        evidence_records=list(records),
    )


def service_with(response_json: str) -> ExtractionService:
    return ExtractionService(FakeLLMClient(responses=[response_json]))


def extract_with(client: FakeLLMClient, *records: EvidenceRecord) -> ExtractionResponse:
    return ExtractionService(client).extract(make_request(*records))


class TestNumericConfidence:
    def test_confidence_095_passes_unchanged(self):
        record = make_evidence("ev-1", "The M1 operates at 24 V DC.")
        response = extract_with(
            FakeLLMClient(
                responses=[
                    '{"items": [{"name": "voltage", "raw_value": "24", '
                    '"unit": "V", "confidence": 0.95, '
                    '"evidence_ids": ["ev-1"]}]}'
                ]
            ),
            record,
        )
        assert len(response.attributes) == 1
        assert response.attributes[0].confidence == 0.95
        assert response.attributes[0].evidence_ids == ["ev-1"]
        assert response.rejected == []

    def test_confidence_zero_and_one_are_valid(self):
        record = make_evidence("ev-1", "The M1 is 24 V and draws 120 W.")
        client = FakeLLMClient(
            responses=[
                '{"items": [{"name": "voltage", "raw_value": "24", '
                '"confidence": 0.0, "evidence_ids": ["ev-1"]}, '
                '{"name": "power", "raw_value": "120", '
                '"confidence": 1.0, "evidence_ids": ["ev-1"]}]}'
            ]
        )
        response = extract_with(client, record)
        assert [a.confidence for a in response.attributes] == [0.0, 1.0]


class TestTextualConfidenceNormalization:
    def test_high_maps_to_09(self):
        record = make_evidence("ev-1", "The M1 operates at 24 V DC.")
        response = extract_with(
            FakeLLMClient(
                responses=[
                    '{"items": [{"name": "voltage", "raw_value": "24", '
                    '"unit": "V", "confidence": "high", '
                    '"evidence_ids": ["ev-1"]}]}'
                ]
            ),
            record,
        )
        assert len(response.attributes) == 1
        attribute = response.attributes[0]
        assert attribute.confidence == 0.9
        assert attribute.name == "voltage"
        assert attribute.raw_value == "24"
        assert attribute.unit == "V"
        assert attribute.evidence_ids == ["ev-1"]
        assert response.rejected == []

    def test_medium_maps_to_06(self):
        record = make_evidence("ev-1", "The M1 is 24 V.")
        response = extract_with(
            FakeLLMClient(
                responses=[
                    '{"items": [{"name": "voltage", "raw_value": "24", '
                    '"confidence": "medium", "evidence_ids": ["ev-1"]}]}'
                ]
            ),
            record,
        )
        assert response.attributes[0].confidence == 0.6

    def test_low_maps_to_03(self):
        record = make_evidence("ev-1", "The M1 is 24 V.")
        response = extract_with(
            FakeLLMClient(
                responses=[
                    '{"items": [{"name": "voltage", "raw_value": "24", '
                    '"confidence": "low", "evidence_ids": ["ev-1"]}]}'
                ]
            ),
            record,
        )
        assert response.attributes[0].confidence == 0.3

    def test_textual_confidence_is_case_and_space_insensitive(self):
        record = make_evidence("ev-1", "The M1 is 24 V.")
        response = extract_with(
            FakeLLMClient(
                responses=[
                    '{"items": [{"name": "voltage", "raw_value": "24", '
                    '"confidence": " HIGH ", "evidence_ids": ["ev-1"]}]}'
                ]
            ),
            record,
        )
        assert response.attributes[0].confidence == 0.9


class TestRejectedConfidence:
    def test_unknown_confidence_string_rejects_only_that_item(self):
        record = make_evidence(
            "ev-1", "The M1 operates at 24 V DC and has IP65 protection."
        )
        response = extract_with(
            FakeLLMClient(
                responses=[
                    '{"items": ['
                    '{"name": "voltage", "raw_value": "24", "unit": "V", '
                    '"confidence": 0.9, "evidence_ids": ["ev-1"]}, '
                    '{"name": "protection_rating", "raw_value": "IP65", '
                    '"confidence": "guaranteed", "evidence_ids": ["ev-1"]}'
                    "]}"
                ]
            ),
            record,
        )
        assert len(response.attributes) == 1
        assert response.attributes[0].name == "voltage"
        assert response.attributes[0].confidence == 0.9
        assert len(response.rejected) == 1
        assert response.rejected[0].name == "protection_rating"
        assert "not a number" in response.rejected[0].reason

    def test_out_of_range_numeric_confidence_rejects_only_that_item(self):
        record = make_evidence(
            "ev-1", "The M1 operates at 24 V DC. Length is 100 mm."
        )
        response = extract_with(
            FakeLLMClient(
                responses=[
                    '{"items": ['
                    '{"name": "voltage", "raw_value": "24", '
                    '"confidence": 0.9, "evidence_ids": ["ev-1"]}, '
                    '{"name": "length", "raw_value": "100", '
                    '"confidence": 1.5, "evidence_ids": ["ev-1"]}'
                    "]}"
                ]
            ),
            record,
        )
        assert len(response.attributes) == 1
        assert response.attributes[0].name == "voltage"
        assert len(response.rejected) == 1
        assert response.rejected[0].name == "length"
        assert "outside the valid range" in response.rejected[0].reason

    def test_boolean_confidence_rejects_that_item(self):
        record = make_evidence("ev-1", "The M1 is 24 V.")
        response = extract_with(
            FakeLLMClient(
                responses=[
                    '{"items": [{"name": "voltage", "raw_value": "24", '
                    '"confidence": true, "evidence_ids": ["ev-1"]}]}'
                ]
            ),
            record,
        )
        assert response.attributes == []
        assert len(response.rejected) == 1
        assert "not a number" in response.rejected[0].reason


class TestMixedAndStructuralSalvage:
    def test_mixed_valid_and_malformed_keeps_valid_items(self):
        record = make_evidence("ev-1", "The M1 is 24 V and IP65.")
        response = extract_with(
            FakeLLMClient(
                responses=[
                    '{"items": ['
                    '{"name": "voltage", "raw_value": "24", "unit": "V", '
                    '"confidence": 0.95, "evidence_ids": ["ev-1"]}, '
                    '{"name": "protection_rating", "raw_value": "IP65", '
                    '"confidence": "high", "evidence_ids": ["ev-1"]}, '
                    '{"name": "broken", "raw_value": "x", "confidence": 0.5, '
                    '"evidence_ids": "not-a-list"}'
                    "]}"
                ]
            ),
            record,
        )
        assert len(response.attributes) == 2
        assert response.attributes[0].confidence == 0.95
        assert response.attributes[1].confidence == 0.9
        assert len(response.rejected) == 1
        assert response.rejected[0].name == "broken"
        assert "failed schema validation" in response.rejected[0].reason

    def test_non_object_item_rejected_safely(self):
        record = make_evidence("ev-1", "The M1 is 24 V.")
        response = extract_with(
            FakeLLMClient(
                responses=[
                    '{"items": ['
                    '{"name": "voltage", "raw_value": "24", '
                    '"confidence": 0.9, "evidence_ids": ["ev-1"]}, '
                    '"not an object"]}'
                ]
            ),
            record,
        )
        assert len(response.attributes) == 1
        assert len(response.rejected) == 1
        assert "not a JSON object" in response.rejected[0].reason

    def test_verbatim_values_preserved_no_fabrication(self):
        record = make_evidence("ev-1", "Length: 100 mm.")
        response = extract_with(
            FakeLLMClient(
                responses=[
                    '{"items": [{"name": "length", "raw_value": "100 mm", '
                    '"normalized_value": "100", "unit": "mm", '
                    '"confidence": "high", "evidence_ids": ["ev-1"], '
                    '"notes": "stated on page"}]}'
                ]
            ),
            record,
        )
        attribute = response.attributes[0]
        assert attribute.raw_value == "100 mm"
        assert attribute.normalized_value == "100"
        assert attribute.unit == "mm"
        assert attribute.notes == "stated on page"

    def test_salvage_does_not_trigger_second_llm_call(self):
        record = make_evidence("ev-1", "The M1 is 24 V.")
        client = FakeLLMClient(
            responses=[
                '{"items": [{"name": "voltage", "raw_value": "24", '
                '"confidence": "high", "evidence_ids": ["ev-1"]}]}'
            ]
        )
        response = extract_with(client, record)
        assert len(response.attributes) == 1
        assert response.attributes[0].confidence == 0.9
        assert len(client.calls) == 1


class TestEvidenceBindingStaysStrict:
    def test_unknown_evidence_id_rejected(self):
        record = make_evidence("ev-1", "The M1 is 24 V.")
        response = extract_with(
            FakeLLMClient(
                responses=[
                    '{"items": [{"name": "voltage", "raw_value": "24", '
                    '"confidence": "high", "evidence_ids": ["ev-99"]}]}'
                ]
            ),
            record,
        )
        assert response.attributes == []
        assert len(response.rejected) == 1
        assert "dangling evidence id" in response.rejected[0].reason

    def test_sibling_product_evidence_id_rejected(self):
        product = make_evidence(
            "ev-1", "The M1 is 24 V.",
            url="https://acme-controls.example/products/M1",
        )
        response = extract_with(
            FakeLLMClient(
                responses=[
                    '{"items": [{"name": "voltage", "raw_value": "36", '
                    '"confidence": "high", "evidence_ids": ["ev-sib"]}]}'
                ]
            ),
            product,
        )
        assert response.attributes == []
        assert len(response.rejected) == 1
        assert "ev-sib" in response.rejected[0].reason

    def test_evidence_ids_used_only_from_accepted_attributes(self):
        record = make_evidence("ev-1", "The M1 is 24 V.")
        response = extract_with(
            FakeLLMClient(
                responses=[
                    '{"items": ['
                    '{"name": "voltage", "raw_value": "24", '
                    '"confidence": "high", "evidence_ids": ["ev-1"]}, '
                    '{"name": "bogus", "raw_value": "x", '
                    '"confidence": "high", "evidence_ids": ["ev-9"]}'
                    "]}"
                ]
            ),
            record,
        )
        assert response.evidence_ids_used == ["ev-1"]


class TestUnsalvageableResponses:
    def test_malformed_entire_response_still_schema_invalid(self):
        record = make_evidence("ev-1", "The M1 is 24 V.")
        client = FakeLLMClient(responses=["this is not json at all"])
        with pytest.raises(ExtractionError) as exc:
            extract_with(client, record)
        assert exc.value.kind == ExtractionErrorKind.SCHEMA_INVALID

    def test_items_not_a_list_still_schema_invalid(self):
        record = make_evidence("ev-1", "The M1 is 24 V.")
        client = FakeLLMClient(
            responses=['{"items": {"name": "voltage", "raw_value": "24"}}']
        )
        with pytest.raises(ExtractionError) as exc:
            extract_with(client, record)
        assert exc.value.kind == ExtractionErrorKind.SCHEMA_INVALID

    def test_provider_failure_still_llm_failed(self):
        record = make_evidence("ev-1", "The M1 is 24 V.")
        client = FakeLLMClient(error=ConnectionError("no network"))
        with pytest.raises(ExtractionError) as exc:
            extract_with(client, record)
        assert exc.value.kind == ExtractionErrorKind.LLM_FAILED