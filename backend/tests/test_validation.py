"""Unit tests for the normalization and validation framework (Step 5).

All tests are offline: no network, no LLM, no official UniHack resources.

GENERIC TEST FIXTURES: attribute names and values below (e.g. "voltage",
"length", "24", "V", "mm") are generic placeholders used ONLY to exercise the
framework. They are NOT UniHack data, NOT official Unilog LOV/UOM values,
and NOT real manufacturer/brand master data. The fake providers used here
are test doubles, not official data.
"""

import pytest
from pydantic import ValidationError

from app.core.domain import AttributeStatus, ValidationStatus
from app.extraction import CandidateAttribute
from app.validation import (
    DefaultNormalizer,
    Severity,
    UnavailableManufacturerBrandProvider,
    UnavailableUOMProvider,
    UnavailableVocabularyProvider,
    ValidationOutcome,
    ValidationService,
    to_domain_attribute_value,
)
from app.validation.lov import LOV_NOT_LOADED_NOTE, AttributeInfo, VocabularyValidation
from app.validation.manufacturer_brand import MASTER_DATA_NOT_LOADED_NOTE
from app.validation.uom import UOM_NOT_LOADED_NOTE, UnitInfo, UomValidation


# --- GENERIC TEST FIXTURES (not UniHack data) --------------------------------

def make_candidate(
    name: str = "voltage",
    raw: str = "24",
    unit: str = "V",
    confidence: float = 0.9,
    evidence_ids: list[str] | tuple[str, ...] = ("ev-1",),
    normalized: str = "",
    notes: str = "",
) -> CandidateAttribute:
    return CandidateAttribute(
        name=name,
        raw_value=raw,
        unit=unit,
        confidence=confidence,
        evidence_ids=list(evidence_ids),
        normalized_value=normalized,
        notes=notes,
    )


def make_service(**providers) -> ValidationService:
    return ValidationService(**providers)


class FakeVocabularyProvider:
    """GENERIC vocabulary double for tests only (not official UniHack LOV).

    Knows two made-up attributes: "voltage" (allowed 24/48) and
    "protection_rating" (allowed IP65).
    """

    def __init__(self) -> None:
        self._allowed = {
            "voltage": {"24", "48"},
            "protection_rating": {"IP65"},
        }

    def is_available(self) -> bool:
        return True

    def find_allowed_attribute(self, attribute_name: str) -> AttributeInfo | None:
        allowed = self._allowed.get(attribute_name)
        if allowed is None:
            return None
        return AttributeInfo(name=attribute_name, allowed_values=sorted(allowed))

    def validate_value(self, attribute_name: str, value: str) -> VocabularyValidation:
        allowed = self._allowed.get(attribute_name)
        if allowed is None:
            return VocabularyValidation(
                valid=None, allowed=None, message=f"attribute {attribute_name!r} not in vocabulary"
            )
        ok = value in allowed
        return VocabularyValidation(
            valid=ok,
            allowed=ok,
            message="" if ok else f"{value!r} not allowed for {attribute_name!r}",
        )

    def normalize_value(self, attribute_name: str, value: str) -> str:
        return value

    def applicable_values_for_classpath(self, classpath: str) -> list[str]:
        return []


class FakeUOMProvider:
    """GENERIC UOM double for tests only (not official UniHack UOM standards)."""

    def __init__(self) -> None:
        self._units = {"V": "volt", "mm": "millimetre", "W": "watt"}

    def is_available(self) -> bool:
        return True

    def lookup_unit(self, unit: str) -> UnitInfo | None:
        canonical = self._units.get(unit)
        if canonical is None:
            return None
        return UnitInfo(unit=unit, canonical_unit=canonical, measurement_type="numeric")

    def normalize_unit(self, unit: str, attribute_name: str = "") -> str:
        return self._units.get(unit, unit)

    def validate_measurement_type(
        self, attribute_name: str, unit: str, value: str
    ) -> UomValidation:
        if unit not in self._units:
            return UomValidation(valid=False, message=f"unit {unit!r} unknown")
        try:
            float(value)
            return UomValidation(
                valid=True, measurement_type="numeric", message="numeric value matches unit"
            )
        except ValueError:
            return UomValidation(
                valid=False,
                measurement_type="numeric",
                message=f"value {value!r} is not numeric",
            )


class TestNormalizationPipeline:
    def test_text_cleanup_trims_and_collapses_whitespace(self):
        candidate = make_candidate(raw="  The   M1  unit  ")
        result = make_service().validate_candidate(candidate, {"ev-1"})
        assert result.normalized_value == "The M1 unit"
        assert "text.cleanup" in result.normalization_applied

    def test_fraction_to_decimal_conversion(self):
        candidate = make_candidate(name="thread", raw="3/4", unit="")
        result = make_service().validate_candidate(candidate, {"ev-1"})
        assert result.normalized_value == "0.75"
        assert "fraction_decimal.convert" in result.normalization_applied

    def test_integer_fraction_converts_to_integer(self):
        candidate = make_candidate(name="ratio", raw="8/4", unit="")
        result = make_service().validate_candidate(candidate, {"ev-1"})
        assert result.normalized_value == "2"

    def test_non_numeric_fraction_left_untouched(self):
        candidate = make_candidate(name="notes", raw="A/B", unit="")
        result = make_service().validate_candidate(candidate, {"ev-1"})
        assert result.normalized_value == ""
        assert result.normalization_applied == []

    def test_zero_denominator_left_untouched(self):
        candidate = make_candidate(name="ratio", raw="1/0", unit="")
        result = make_service().validate_candidate(candidate, {"ev-1"})
        assert result.normalization_applied == []

    def test_original_raw_value_always_preserved(self):
        candidate = make_candidate(raw="  24  ")
        result = make_service().validate_candidate(candidate, {"ev-1"})
        assert result.raw_value == "  24  "
        assert result.normalized_value == "24"

    def test_ai_provided_normalized_value_kept_without_official_rule(self):
        candidate = make_candidate(raw="24", normalized="24.0")
        result = make_service().validate_candidate(candidate, {"ev-1"})
        assert result.normalized_value == "24.0"
        assert any(m.code == "normalization.ai_provided" for m in result.messages)

    def test_unit_normalization_delegates_when_uom_available(self):
        normalizer = DefaultNormalizer(uom_provider=FakeUOMProvider())
        result = make_service(normalizer=normalizer).validate_candidate(
            make_candidate(unit="mm"), {"ev-1"}
        )
        assert normalizer.normalize_unit("mm") == "millimetre"

    def test_unit_normalization_unchanged_when_uom_unavailable(self):
        normalizer = DefaultNormalizer()  # unavailable UOM provider
        assert normalizer.normalize_unit("mm") == "mm"

    def test_manufacturer_and_brand_are_not_alias_mapped_without_master_data(self):
        normalizer = DefaultNormalizer()
        assert normalizer.normalize_manufacturer("  Acme  Corp ") == "Acme Corp"
        assert normalizer.normalize_brand("  Acme  Pro ") == "Acme Pro"

    def test_category_specific_normalization_is_identity_without_official_rules(self):
        normalizer = DefaultNormalizer()
        assert normalizer.normalize_category_specific("fittings", " X ") == "X"


class TestUnavailableVocabulary:
    def test_provider_reports_data_not_loaded(self):
        provider = UnavailableVocabularyProvider()
        assert provider.is_available() is False
        result = provider.validate_value("voltage", "24")
        assert result.valid is None
        assert result.allowed is None
        assert result.message == LOV_NOT_LOADED_NOTE

    def test_find_allowed_attribute_returns_none(self):
        provider = UnavailableVocabularyProvider()
        assert provider.find_allowed_attribute("voltage") is None
        assert provider.applicable_values_for_classpath("fittings") == []
        assert provider.normalize_value("voltage", "24") == "24"

    def test_service_reports_not_loaded_message(self):
        result = make_service().validate_candidate(make_candidate(), {"ev-1"})
        messages = {m.code: m.message for m in result.messages}
        assert "vocab.not_loaded" in messages
        assert messages["vocab.not_loaded"] == LOV_NOT_LOADED_NOTE

    def test_not_loaded_is_never_a_pass(self):
        result = make_service().validate_candidate(make_candidate(), {"ev-1"})
        assert result.outcome is ValidationOutcome.NOT_VALIDATED
        assert result.outcome is not ValidationOutcome.VERIFIED


class TestUnavailableUOM:
    def test_provider_reports_data_not_loaded(self):
        provider = UnavailableUOMProvider()
        assert provider.is_available() is False
        result = provider.validate_measurement_type("voltage", "V", "24")
        assert result.valid is None
        assert result.message == UOM_NOT_LOADED_NOTE
        assert provider.lookup_unit("V") is None
        assert provider.normalize_unit("V") == "V"

    def test_service_reports_not_loaded_when_unit_present(self):
        result = make_service().validate_candidate(make_candidate(), {"ev-1"})
        messages = {m.code: m.message for m in result.messages}
        assert "uom.not_loaded" in messages
        assert messages["uom.not_loaded"] == UOM_NOT_LOADED_NOTE

    def test_no_unit_skips_uom_check(self):
        result = make_service().validate_candidate(
            make_candidate(unit=""), {"ev-1"}
        )
        assert not any(m.source == "uom" for m in result.messages)

    def test_unit_not_verified_without_official_data(self):
        result = make_service().validate_candidate(make_candidate(), {"ev-1"})
        assert result.outcome is ValidationOutcome.NOT_VALIDATED


class TestUnavailableManufacturerBrand:
    def test_provider_reports_master_data_not_loaded(self):
        provider = UnavailableManufacturerBrandProvider()
        assert provider.is_available() is False
        match = provider.match_manufacturer("Acme Controls")
        assert match.matched is False
        assert match.message == MASTER_DATA_NOT_LOADED_NOTE
        brand = provider.match_brand("Acme")
        assert brand.matched is False
        assert brand.message == MASTER_DATA_NOT_LOADED_NOTE

    def test_canonical_functions_do_not_alias_without_master_data(self):
        provider = UnavailableManufacturerBrandProvider()
        assert provider.canonical_manufacturer("Acme") == "Acme"
        assert provider.canonical_brand("Acme") == "Acme"


class TestEvidenceReferencePreservation:
    def test_evidence_refs_preserved_verbatim(self):
        candidate = make_candidate(evidence_ids=["ev-1", "ev-2"])
        result = make_service().validate_candidate(candidate, {"ev-1", "ev-2"})
        assert result.evidence_refs == ["ev-1", "ev-2"]

    def test_evidence_refs_survive_normalization(self):
        candidate = make_candidate(raw="  24  ", evidence_ids=["ev-1", "ev-2"])
        result = make_service().validate_candidate(candidate, {"ev-1", "ev-2"})
        assert result.normalized_value == "24"
        assert result.evidence_refs == ["ev-1", "ev-2"]

    def test_evidence_refs_survive_domain_mapping(self):
        candidate = make_candidate(evidence_ids=["ev-1", "ev-2"])
        validated = make_service().validate_candidate(candidate, {"ev-1", "ev-2"})
        domain_value = to_domain_attribute_value(validated)
        assert domain_value.evidence_refs == ["ev-1", "ev-2"]

    def test_dangling_evidence_reference_makes_invalid(self):
        candidate = make_candidate(evidence_ids=["ev-1", "ev-99"])
        result = make_service().validate_candidate(candidate, {"ev-1"})
        assert result.outcome is ValidationOutcome.INVALID
        assert any(m.code == "evidence.dangling_references" for m in result.messages)
        assert "ev-99" in next(
            m.message for m in result.messages if m.code == "evidence.dangling_references"
        )


class TestInvalidStructuralData:
    def test_empty_name_is_invalid(self):
        candidate = make_candidate(name="  ")
        result = make_service().validate_candidate(candidate, {"ev-1"})
        assert result.outcome is ValidationOutcome.INVALID
        assert any(m.code == "structural.empty_name" for m in result.messages)

    def test_empty_value_is_invalid(self):
        candidate = make_candidate(raw="   ", unit="")
        result = make_service().validate_candidate(candidate, {"ev-1"})
        assert result.outcome is ValidationOutcome.INVALID
        assert any(m.code == "structural.empty_value" for m in result.messages)

    def test_confidence_out_of_range_is_invalid(self):
        # model_construct bypasses Pydantic's own 0..1 bound so the service's
        # defensive structural check can be exercised.
        candidate = CandidateAttribute.model_construct(
            name="voltage", raw_value="24", unit="V", confidence=1.5, evidence_ids=["ev-1"]
        )
        result = make_service().validate_candidate(candidate, {"ev-1"})
        assert result.outcome is ValidationOutcome.INVALID
        assert any(m.code == "structural.confidence_out_of_range" for m in result.messages)

    def test_unit_without_value_is_invalid(self):
        candidate = make_candidate(raw="", unit="V")
        result = make_service().validate_candidate(candidate, {"ev-1"})
        assert result.outcome is ValidationOutcome.INVALID
        assert any(m.code == "structural.unit_without_value" for m in result.messages)

    def test_model_rejects_attributes_without_evidence(self):
        with pytest.raises(ValidationError):
            CandidateAttribute(name="voltage", raw_value="24", evidence_ids=[])


class TestStatusTransitions:
    def test_all_checks_pass_with_official_data_is_verified(self):
        service = make_service(
            vocabulary_provider=FakeVocabularyProvider(),
            uom_provider=FakeUOMProvider(),
        )
        result = service.validate_candidate(make_candidate(), {"ev-1"})
        assert result.outcome is ValidationOutcome.VERIFIED
        assert any(m.code == "vocab.value_allowed" for m in result.messages)
        assert any(m.code == "uom.unit_verified" for m in result.messages)

    def test_value_not_in_vocabulary_is_invalid(self):
        service = make_service(vocabulary_provider=FakeVocabularyProvider())
        candidate = make_candidate(raw="220")  # not an allowed voltage
        result = service.validate_candidate(candidate, {"ev-1"})
        assert result.outcome is ValidationOutcome.INVALID
        assert any(m.code == "vocab.value_not_allowed" for m in result.messages)

    def test_unknown_attribute_is_needs_review(self):
        service = make_service(vocabulary_provider=FakeVocabularyProvider())
        candidate = make_candidate(name="no_such_attribute")
        result = service.validate_candidate(candidate, {"ev-1"})
        assert result.outcome is ValidationOutcome.NEEDS_REVIEW
        assert any(m.code == "vocab.unknown_attribute" for m in result.messages)

    def test_unknown_unit_is_invalid(self):
        service = make_service(uom_provider=FakeUOMProvider())
        candidate = make_candidate(unit="parsec")
        result = service.validate_candidate(candidate, {"ev-1"})
        assert result.outcome is ValidationOutcome.INVALID
        assert any(m.code == "uom.unknown_unit" for m in result.messages)

    def test_measurement_type_mismatch_is_invalid(self):
        service = make_service(uom_provider=FakeUOMProvider())
        candidate = make_candidate(raw="hello", unit="V")
        result = service.validate_candidate(candidate, {"ev-1"})
        assert result.outcome is ValidationOutcome.INVALID
        assert any(m.code == "uom.measurement_type_mismatch" for m in result.messages)

    def test_not_validated_when_only_vocabulary_available_but_value_allowed(self):
        # UOM unavailable -> cannot be VERIFIED even though vocab passed.
        service = make_service(vocabulary_provider=FakeVocabularyProvider())
        result = service.validate_candidate(make_candidate(), {"ev-1"})
        assert result.outcome is ValidationOutcome.NOT_VALIDATED
        assert any(m.code == "vocab.value_allowed" for m in result.messages)

    def test_invalid_beats_all_other_outcomes(self):
        service = make_service(
            vocabulary_provider=FakeVocabularyProvider(),
            uom_provider=FakeUOMProvider(),
        )
        candidate = make_candidate(evidence_ids=["ev-99"])
        result = service.validate_candidate(candidate, {"ev-1"})
        assert result.outcome is ValidationOutcome.INVALID

    def test_summary_counts_outcomes(self):
        service = make_service()  # nothing available
        summary = service.validate(
            [make_candidate(), make_candidate(raw="", unit="")],
            {"ev-1"},
        )
        assert summary.counts[ValidationOutcome.NOT_VALIDATED.value] == 1
        assert summary.counts[ValidationOutcome.INVALID.value] == 1


class TestNoFalseVerified:
    def test_perfect_attribute_without_official_data_is_never_verified(self):
        candidate = make_candidate(
            raw="24", unit="V", confidence=1.0, evidence_ids=["ev-1"]
        )
        result = make_service().validate_candidate(candidate, {"ev-1"})
        assert result.outcome is ValidationOutcome.NOT_VALIDATED

    def test_empty_result_does_not_imply_verified(self):
        summary = make_service().validate([], set())
        assert summary.attributes == []
        assert summary.counts == {}

    def test_nothing_is_verified_when_any_check_unavailable(self):
        # vocab available, UOM not -> value allowed but still not VERIFIED.
        service = make_service(vocabulary_provider=FakeVocabularyProvider())
        for candidate in (
            make_candidate(),
            make_candidate(raw="48"),
        ):
            result = service.validate_candidate(candidate, {"ev-1"})
            assert result.outcome is not ValidationOutcome.VERIFIED


class TestDomainMapping:
    def test_outcome_maps_to_attribute_status(self):
        candidate = make_candidate(evidence_ids=["ev-99"])
        validated = make_service().validate_candidate(candidate, {"ev-1"})
        domain_value = to_domain_attribute_value(validated)
        assert domain_value.status is AttributeStatus.REJECTED

    def test_not_validated_maps_to_extracted_status(self):
        validated = make_service().validate_candidate(make_candidate(), {"ev-1"})
        domain_value = to_domain_attribute_value(validated)
        assert domain_value.status is AttributeStatus.EXTRACTED

    def test_messages_map_to_domain_validation_results(self):
        candidate = make_candidate(evidence_ids=["ev-99"])
        validated = make_service().validate_candidate(candidate, {"ev-1"})
        domain_value = to_domain_attribute_value(validated)
        assert domain_value.validation_results
        assert any(
            result.status is ValidationStatus.FAILED
            for result in domain_value.validation_results
        )

    def test_message_codes_are_stable_and_explain_why(self):
        result = make_service().validate_candidate(make_candidate(), {"ev-1"})
        codes = {m.code for m in result.messages}
        assert "vocab.not_loaded" in codes
        assert "uom.not_loaded" in codes
        for message in result.messages:
            assert message.message  # every message explains WHY
