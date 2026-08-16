"""Unit tests for evidence-based extraction (Step 4B).

All tests use the offline FakeLLMClient with canned responses - no real LLM
API and no network calls are ever made.

TEST FIXTURES: made-up evidence text used only to exercise extraction logic.
These are NOT UniHack data and NOT real manufacturer data.
"""

import pytest
from pydantic import ValidationError

from app.core.domain import ConflictStatus, ProductIdentity, SourceType
from app.extraction import (
    ExtractionError,
    ExtractionErrorKind,
    ExtractionRequest,
    ExtractionResponse,
    ExtractionService,
    to_domain_attribute_values,
)
from app.llm import FakeLLMClient, LLMProviderUnavailableError
from app.sources.retrieval import EvidenceRecord, RetrievalStatus


# --- TEST FIXTURES (not UniHack data, not real manufacturers) ----------------

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


class TestSuccessfulExtraction:
    def test_successful_attribute_extraction(self):
        records = [
            make_evidence("ev-1", "The M1 operates at 24 V DC."),
            make_evidence("ev-2", "The M1 has IP65 protection."),
        ]
        service = service_with(
            '{"items": ['
            '{"name": "voltage", "raw_value": "24", "unit": "V", '
            '"confidence": 0.9, "evidence_ids": ["ev-1"], '
            '"notes": "stated on the product page"}, '
            '{"name": "protection_rating", "raw_value": "IP65", '
            '"confidence": 0.8, "evidence_ids": ["ev-2"]}'
            "]}"
        )
        response = service.extract(make_request(*records))

        assert response.rejected == []
        assert len(response.attributes) == 2
        voltage = response.attributes[0]
        assert voltage.name == "voltage"
        assert voltage.raw_value == "24"
        assert voltage.unit == "V"
        assert voltage.confidence == 0.9
        assert voltage.evidence_ids == ["ev-1"]
        assert response.evidence_ids_used == ["ev-1", "ev-2"]

    def test_multiple_evidence_records_cross_cited(self):
        records = [
            make_evidence("ev-1", "The M1 operates at 24 V."),
            make_evidence("ev-2", "M1 datasheet: 24 V DC supply."),
        ]
        service = service_with(
            '{"items": [{"name": "voltage", "raw_value": "24", "unit": "V", '
            '"confidence": 0.95, "evidence_ids": ["ev-1", "ev-2"]}]}'
        )
        response = service.extract(make_request(*records))
        assert len(response.attributes) == 1
        assert response.attributes[0].evidence_ids == ["ev-1", "ev-2"]

    def test_normalized_value_preserved_when_evidence_supported(self):
        record = make_evidence("ev-1", "Length: 100 mm.")
        service = service_with(
            '{"items": [{"name": "length", "raw_value": "100 mm", '
            '"normalized_value": "100", "unit": "mm", "confidence": 0.9, '
            '"evidence_ids": ["ev-1"]}]}'
        )
        response = service.extract(make_request(record))
        attribute = response.attributes[0]
        assert attribute.normalized_value == "100"
        assert attribute.unit == "mm"

    def test_empty_items_is_a_valid_empty_result(self):
        record = make_evidence("ev-1", "No attributes here.")
        response = service_with('{"items": []}').extract(make_request(record))
        assert response.attributes == []
        assert response.rejected == []
        assert response.evidence_ids_used == []

    def test_notes_are_length_capped(self):
        record = make_evidence("ev-1", "The M1 is 24 V.")
        service = service_with(
            '{"items": [{"name": "voltage", "raw_value": "24", '
            '"confidence": 0.5, "evidence_ids": ["ev-1"], '
            '"notes": "' + "x" * 500 + '"}]}'
        )
        response = service.extract(make_request(record))
        assert len(response.attributes[0].notes) <= 200


class TestRejectedClaims:
    def test_attribute_without_evidence_rejected(self):
        record = make_evidence("ev-1", "The M1 is 24 V.")
        service = service_with(
            '{"items": ['
            '{"name": "voltage", "raw_value": "24", "confidence": 0.9, "evidence_ids": ["ev-1"]}, '
            '{"name": "length", "raw_value": "100", "confidence": 0.9, "evidence_ids": []}'
            "]}"
        )
        response = service.extract(make_request(record))
        assert len(response.attributes) == 1
        assert response.attributes[0].name == "voltage"
        assert len(response.rejected) == 1
        assert response.rejected[0].name == "length"
        assert "no evidence_ids" in response.rejected[0].reason

    def test_dangling_evidence_id_rejected(self):
        record = make_evidence("ev-1", "The M1 is 24 V.")
        service = service_with(
            '{"items": [{"name": "voltage", "raw_value": "24", '
            '"evidence_ids": ["ev-99"]}]}'
        )
        response = service.extract(make_request(record))
        assert response.attributes == []
        assert len(response.rejected) == 1
        assert "dangling evidence id" in response.rejected[0].reason
        assert "ev-99" in response.rejected[0].reason

    def test_partially_dangling_evidence_id_rejected(self):
        record = make_evidence("ev-1", "The M1 is 24 V.")
        service = service_with(
            '{"items": [{"name": "voltage", "raw_value": "24", '
            '"evidence_ids": ["ev-1", "ev-88"]}]}'
        )
        response = service.extract(make_request(record))
        assert response.attributes == []
        assert "ev-88" in response.rejected[0].reason

    def test_no_hallucinated_value_without_evidence(self):
        record = make_evidence("ev-1", "The M1 is 24 V.")
        service = service_with(
            '{"items": [{"name": "certification", "raw_value": "ATEX Zone 1", '
            '"confidence": 0.99, "evidence_ids": []}]}'
        )
        response = service.extract(make_request(record))
        assert response.attributes == []
        assert len(response.rejected) == 1
        assert response.rejected[0].name == "certification"

    def test_empty_value_with_evidence_rejected(self):
        record = make_evidence("ev-1", "The M1 is 24 V.")
        service = service_with(
            '{"items": [{"name": "voltage", "raw_value": "  ", '
            '"evidence_ids": ["ev-1"]}]}'
        )
        response = service.extract(make_request(record))
        assert response.attributes == []
        assert "empty value" in response.rejected[0].reason

    def test_request_without_evidence_rejected_at_construction(self):
        with pytest.raises(ValidationError):
            ExtractionRequest(
                identity=ProductIdentity(mpn="M1"), evidence_records=[]
            )


class TestInvalidLlmOutput:
    def test_invalid_confidence_rejected_per_item(self):
        record = make_evidence("ev-1", "The M1 is 24 V.")
        service = service_with(
            '{"items": [{"name": "voltage", "raw_value": "24", '
            '"confidence": 1.5, "evidence_ids": ["ev-1"]}]}'
        )
        response = service.extract(make_request(record))
        assert response.attributes == []
        assert len(response.rejected) == 1
        assert response.rejected[0].name == "voltage"
        assert "outside the valid range" in response.rejected[0].reason

    def test_malformed_llm_response_handled_safely(self):
        record = make_evidence("ev-1", "The M1 is 24 V.")
        service = service_with("this is not json at all")
        with pytest.raises(ExtractionError) as exc:
            service.extract(make_request(record))
        assert exc.value.kind == ExtractionErrorKind.SCHEMA_INVALID
        assert "not valid JSON" in str(exc.value) or "schema validation" in str(exc.value)

    def test_provider_failure_mapped(self):
        record = make_evidence("ev-1", "The M1 is 24 V.")
        client = FakeLLMClient(error=ConnectionError("no network"))
        with pytest.raises(ExtractionError) as exc:
            ExtractionService(client).extract(make_request(record))
        assert exc.value.kind == ExtractionErrorKind.LLM_FAILED


class TestConflicts:
    def test_conflicting_evidence_produces_multiple_candidates(self):
        records = [
            make_evidence("ev-1", "The M1 is 24 V."),
            make_evidence("ev-2", "The M1 runs on 48 V."),
        ]
        service = service_with(
            '{"items": ['
            '{"name": "voltage", "raw_value": "24", "unit": "V", '
            '"confidence": 0.9, "evidence_ids": ["ev-1"]}, '
            '{"name": "voltage", "raw_value": "48", "unit": "V", '
            '"confidence": 0.8, "evidence_ids": ["ev-2"]}'
            "]}"
        )
        response = service.extract(make_request(*records))

        voltage_candidates = [a for a in response.attributes if a.name == "voltage"]
        assert len(voltage_candidates) == 2
        assert {a.raw_value for a in voltage_candidates} == {"24", "48"}

        domain_values = to_domain_attribute_values(response)
        assert domain_values["voltage"].conflict_status == ConflictStatus.CONFLICT
        assert len(domain_values["voltage"].candidates) == 2


class TestPrompt:
    def test_prompt_includes_evidence_ids_and_instructions(self):
        client = FakeLLMClient(responses=['{"items": []}'])
        service = ExtractionService(client)
        service.extract(make_request(make_evidence("ev-1", "The M1 is 24 V.")))
        prompt = client.calls[0]
        assert "ev-1" in prompt
        assert "Use ONLY the evidence" in prompt
        assert "Do not guess" in prompt
        assert "evidence_ids" in prompt
        assert "M1" in prompt
        assert "Acme Controls" in prompt

    def test_prompt_shows_the_raw_evidence_id(self):
        client = FakeLLMClient(responses=['{"items": []}'])
        service = ExtractionService(client)
        service.extract(make_request(make_evidence("ev-1", "The M1 is 24 V.")))
        prompt = client.calls[0]
        assert "[ev-1] Fixture page" in prompt
        assert "[ev-ev-1]" not in prompt


class TestBulletFallback:
    def test_bullet_output_parsed_with_evidence_binding(self):
        record = make_evidence("ev-1", "The M1 operates at 24 V.")
        client = FakeLLMClient(
            responses=[
                "this is definitely not json",
                "- Voltage: 24 V [ev-1]\n- Motor Type: Brushless [ev-1]\n"
                "Notes: all from the page",
            ]
        )
        service = ExtractionService(client)
        response = service.extract(make_request(record))
        assert len(response.attributes) == 2
        assert response.attributes[0].name == "Voltage"
        assert response.attributes[0].raw_value == "24 V"
        assert response.attributes[0].evidence_ids == ["ev-1"]
        assert response.attributes[1].name == "Motor Type"
        assert response.attributes[1].confidence == 0.0

    def test_bullet_with_unknown_evidence_id_is_dropped(self):
        record = make_evidence("ev-1", "The M1 operates at 24 V.")
        client = FakeLLMClient(
            responses=[
                "not json either",
                "- Voltage: 24 V [ev-99]\n- Length: 100 mm [ev-1]",
            ]
        )
        service = ExtractionService(client)
        response = service.extract(make_request(record))
        assert len(response.attributes) == 1
        assert response.attributes[0].name == "Length"

    def test_bullet_without_usable_evidence_raises_schema_invalid(self):
        record = make_evidence("ev-1", "The M1 operates at 24 V.")
        client = FakeLLMClient(responses=["not json", "just prose, no bullets"])
        service = ExtractionService(client)
        with pytest.raises(ExtractionError) as exc:
            service.extract(make_request(record))
        assert exc.value.kind == ExtractionErrorKind.SCHEMA_INVALID


class TestDomainMapping:
    def test_mapping_resolves_evidence_refs(self):
        records = [
            make_evidence("ev-1", "The M1 operates at 24 V."),
            make_evidence("ev-2", "The M1 has IP65 protection."),
        ]
        service = service_with(
            '{"items": ['
            '{"name": "voltage", "raw_value": "24", "unit": "V", '
            '"confidence": 0.9, "evidence_ids": ["ev-1"]}, '
            '{"name": "protection_rating", "raw_value": "IP65", '
            '"confidence": 0.8, "evidence_ids": ["ev-2"]}'
            "]}"
        )
        response = service.extract(make_request(*records))
        domain_values = to_domain_attribute_values(response)

        assert set(domain_values) == {"voltage", "protection_rating"}
        known = {"ev-1", "ev-2"}
        for value in domain_values.values():
            assert set(value.evidence_refs) <= known
            assert len(value.candidates) >= 1
            assert value.conflict_status == ConflictStatus.AGREEMENT
        assert domain_values["voltage"].raw_value == "24"
        assert isinstance(response, ExtractionResponse)


class TestEvidenceQuotes:
    """Step 8B: exact quotes resolved from the retrieved evidence text."""

    def test_quote_contains_the_exact_supporting_text(self):
        record = make_evidence(
            "ev-1", "The M1 operates at 24 V DC with IP65 protection."
        )
        service = service_with(
            '{"items": [{"name": "voltage", "raw_value": "24 V DC", '
            '"confidence": 0.9, "evidence_ids": ["ev-1"]}]}'
        )
        attribute = service.extract(make_request(record)).attributes[0]
        assert attribute.quote != ""
        assert "24 V DC" in attribute.quote
        assert attribute.quote.startswith("The M1 operates at")

    def test_quote_prefers_normalized_value(self):
        record = make_evidence(
            "ev-1",
            "Length is 100 mm (approximately ten centimeters).",
        )
        service = service_with(
            '{"items": [{"name": "length", "raw_value": "10 cm", '
            '"normalized_value": "100 mm", "unit": "mm", "confidence": 0.9, '
            '"evidence_ids": ["ev-1"]}]}'
        )
        attribute = service.extract(make_request(record)).attributes[0]
        assert "100 mm" in attribute.quote
        assert "10 cm" not in attribute.quote

    def test_quote_tolerates_whitespace_differences(self):
        record = make_evidence(
            "ev-1", "Voltage rating:\n  24 V\n  DC input."
        )
        service = service_with(
            '{"items": [{"name": "voltage", "raw_value": "24 V DC", '
            '"confidence": 0.9, "evidence_ids": ["ev-1"]}]}'
        )
        attribute = service.extract(make_request(record)).attributes[0]
        assert "24 V DC" in attribute.quote

    def test_quote_unavailable_when_value_is_not_in_the_text(self):
        record = make_evidence("ev-1", "The M1 has IP65 protection.")
        service = service_with(
            '{"items": [{"name": "voltage", "raw_value": "24 V DC", '
            '"confidence": 0.9, "evidence_ids": ["ev-1"]}]}'
        )
        attribute = service.extract(make_request(record)).attributes[0]
        assert attribute.quote == ""

    def test_quote_unavailable_for_empty_evidence_text(self):
        record = make_evidence("ev-1", "")
        service = service_with(
            '{"items": [{"name": "voltage", "raw_value": "24 V", '
            '"confidence": 0.9, "evidence_ids": ["ev-1"]}]}'
        )
        attribute = service.extract(make_request(record)).attributes[0]
        assert attribute.quote == ""

    def test_quote_uses_the_earliest_occurrence(self):
        record = make_evidence(
            "ev-1", "24 V supply. Compatibility: 24 V systems only."
        )
        service = service_with(
            '{"items": [{"name": "voltage", "raw_value": "24 V", '
            '"confidence": 0.9, "evidence_ids": ["ev-1"]}]}'
        )
        attribute = service.extract(make_request(record)).attributes[0]
        assert attribute.quote.count("24 V") >= 1
        assert not attribute.quote.startswith("Compatibility")

    def test_quote_is_length_capped(self):
        record = make_evidence(
            "ev-1",
            "x" * 300 + " 24 V DC " + "y" * 300,
        )
        service = service_with(
            '{"items": [{"name": "voltage", "raw_value": "24 V DC", '
            '"confidence": 0.9, "evidence_ids": ["ev-1"]}]}'
        )
        attribute = service.extract(make_request(record)).attributes[0]
        assert attribute.quote != ""
        assert len(attribute.quote) <= 210

    def test_notes_are_preserved_alongside_the_quote(self):
        record = make_evidence("ev-1", "The M1 operates at 24 V DC.")
        service = service_with(
            '{"items": [{"name": "voltage", "raw_value": "24 V DC", '
            '"confidence": 0.9, "evidence_ids": ["ev-1"], '
            '"notes": "per the technical datasheet"}]}'
        )
        attribute = service.extract(make_request(record)).attributes[0]
        assert attribute.quote != ""
        assert attribute.notes == "per the technical datasheet"
