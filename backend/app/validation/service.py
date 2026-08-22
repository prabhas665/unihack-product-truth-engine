"""Validation service (Step 5).

Combines, per candidate attribute:
1. structural validation        - name/value/confidence/unit sanity
2. evidence-reference validation - every claim stays traceable to evidence
3. normalization checks         - deterministic normalization is applied and
                                  recorded; the original raw value is kept
4. vocabulary (LOV) validation  - only when official data is AVAILABLE
5. UOM validation               - only when official data is AVAILABLE

Verdict derivation guarantees NO false VERIFIED results: an attribute is
VERIFIED only when every applicable check passed against an AVAILABLE
official resource. With the unavailable providers everything stays
NOT_VALIDATED (never VERIFIED); hard errors yield INVALID; inconclusive or
attention-worthy situations yield NEEDS_REVIEW.
"""

from __future__ import annotations

from app.core.domain import (
    AttributeStatus,
    AttributeValue,
    ValidationResult,
    ValidationStatus,
    ValidationType,
)
from app.extraction import CandidateAttribute
from app.validation.lov import (
    LOV_NOT_LOADED_NOTE,
    UnavailableVocabularyProvider,
    VocabularyProvider,
    VocabularyValidation,
)
from app.validation.manufacturer_brand import (
    ManufacturerBrandProvider,
    UnavailableManufacturerBrandProvider,
)
from app.validation.normalizer import DefaultNormalizer, Normalizer
from app.validation.types import (
    Severity,
    ValidatedAttribute,
    ValidationMessage,
    ValidationOutcome,
    ValidationSummary,
)
from app.validation.uom import (
    UOM_NOT_LOADED_NOTE,
    UOMProvider,
    UnavailableUOMProvider,
    UomValidation,
)


class ValidationService:
    """Validates extracted candidate attributes.

    All providers default to the unavailable implementations, so the service
    is safe to use before any official UniHack data is loaded.
    """

    def __init__(
        self,
        normalizer: Normalizer | None = None,
        vocabulary_provider: VocabularyProvider | None = None,
        uom_provider: UOMProvider | None = None,
        manufacturer_brand_provider: ManufacturerBrandProvider | None = None,
    ) -> None:
        self._normalizer = normalizer or DefaultNormalizer()
        self._vocab = vocabulary_provider or UnavailableVocabularyProvider()
        self._uom = uom_provider or UnavailableUOMProvider()
        # Held for future identity checks; nothing official is available yet.
        self._manufacturer_brand = (
            manufacturer_brand_provider or UnavailableManufacturerBrandProvider()
        )

    def validate(
        self,
        candidates: list[CandidateAttribute],
        known_evidence_ids: set[str],
    ) -> ValidationSummary:
        """Validate every candidate attribute and summarize the results."""
        validated = [
            self.validate_candidate(candidate, known_evidence_ids)
            for candidate in candidates
        ]
        counts: dict[str, int] = {}
        for attribute in validated:
            counts[attribute.outcome.value] = counts.get(attribute.outcome.value, 0) + 1
        return ValidationSummary(attributes=validated, counts=counts)

    def validate_candidate(
        self,
        candidate: CandidateAttribute,
        known_evidence_ids: set[str],
    ) -> ValidatedAttribute:
        """Run all checks on one candidate attribute."""
        messages: list[ValidationMessage] = []

        self._check_structure(candidate, messages)
        self._check_evidence(candidate, known_evidence_ids, messages)
        normalized_value, applied = self._normalize(candidate, messages)
        vocab_result = self._check_vocabulary(candidate, messages)
        uom_result = self._check_uom(candidate, messages)

        outcome = _derive_outcome(messages, vocab_result, uom_result)
        return ValidatedAttribute(
            name=candidate.name,
            raw_value=candidate.raw_value,  # original AI value, always preserved
            normalized_value=normalized_value,
            unit=candidate.unit,
            # Clamped so the output record stays valid; an out-of-range input
            # is already flagged INVALID by the structural check above.
            confidence=min(1.0, max(0.0, candidate.confidence)),
            evidence_refs=list(candidate.evidence_ids),
            outcome=outcome,
            messages=messages,
            normalization_applied=applied,
        )

    # --- individual checks -------------------------------------------------

    @staticmethod
    def _check_structure(
        candidate: CandidateAttribute, messages: list[ValidationMessage]
    ) -> None:
        if not candidate.name.strip():
            messages.append(
                _message(
                    "structural",
                    "structural.empty_name",
                    Severity.ERROR,
                    "attribute name is empty",
                )
            )
        raw = candidate.raw_value.strip()
        normalized = candidate.normalized_value.strip()
        if not raw and not normalized:
            messages.append(
                _message(
                    "structural",
                    "structural.empty_value",
                    Severity.ERROR,
                    "value is empty (both raw_value and normalized_value are blank)",
                )
            )
        if not (0.0 <= candidate.confidence <= 1.0):
            messages.append(
                _message(
                    "structural",
                    "structural.confidence_out_of_range",
                    Severity.ERROR,
                    f"confidence {candidate.confidence} is outside [0, 1]",
                )
            )
        if candidate.unit.strip() and not raw:
            messages.append(
                _message(
                    "structural",
                    "structural.unit_without_value",
                    Severity.ERROR,
                    "unit is set but the raw value is empty",
                )
            )
        if normalized and not raw:
            messages.append(
                _message(
                    "structural",
                    "structural.normalized_without_raw",
                    Severity.ERROR,
                    "normalized_value is set but raw_value is empty",
                )
            )

    @staticmethod
    def _check_evidence(
        candidate: CandidateAttribute,
        known_evidence_ids: set[str],
        messages: list[ValidationMessage],
    ) -> None:
        used = [eid for eid in candidate.evidence_ids if eid]
        if not used:
            messages.append(
                _message(
                    "evidence",
                    "evidence.no_references",
                    Severity.ERROR,
                    "no evidence references: the value is not traceable to any supplied evidence",
                )
            )
            return
        dangling = sorted({eid for eid in used if eid not in known_evidence_ids})
        if dangling:
            messages.append(
                _message(
                    "evidence",
                    "evidence.dangling_references",
                    Severity.ERROR,
                    f"evidence references {dangling} are not among the supplied evidence",
                )
            )

    def _normalize(
        self, candidate: CandidateAttribute, messages: list[ValidationMessage]
    ) -> tuple[str, list[str]]:
        """Deterministic normalization; the original raw value is never lost."""
        applied: list[str] = []
        text = self._normalizer.normalize_text(candidate.raw_value)
        if text != candidate.raw_value:
            applied.append("text.cleanup")
        # Fraction normalization: only for measurement fields with explicit
        # numeric units; categorical/text (e.g., Hub Type) must remain textual.
        # Conservative: Hub Type and similar categorical attributes never convert.
        name_lower = candidate.name.lower().strip()
        is_categorical = ("hub" in name_lower and "type" in name_lower) or name_lower in {
            "hub type",
            "hub_type",
            "hubtype",
        }
        if is_categorical:
            converted = text
        else:
            converted = self._normalizer.normalize_fraction_decimal(text)
            if converted != text:
                applied.append("fraction_decimal.convert")
        if applied:
            messages.append(
                _message(
                    "normalization",
                    "normalization.applied",
                    Severity.INFO,
                    f"normalized {candidate.raw_value!r} -> {converted!r} "
                    f"(steps: {', '.join(applied)})",
                )
            )
            return converted, applied
        if is_categorical and text:
            # For categorical with no fraction conversion, preserve cleaned text
            return text, applied
        if candidate.normalized_value.strip():
            messages.append(
                _message(
                    "normalization",
                    "normalization.ai_provided",
                    Severity.INFO,
                    "AI-provided normalized value kept (no official normalization rule to verify it)",
                )
            )
            return candidate.normalized_value, []
        return "", []

    def _check_vocabulary(
        self, candidate: CandidateAttribute, messages: list[ValidationMessage]
    ) -> VocabularyValidation:
        """LOV check; reports 'not loaded' explicitly when data is missing."""
        if not self._vocab.is_available():
            messages.append(
                _message("vocab", "vocab.not_loaded", Severity.INFO, LOV_NOT_LOADED_NOTE)
            )
            return VocabularyValidation(valid=None, allowed=None, message=LOV_NOT_LOADED_NOTE)
        info = self._vocab.find_allowed_attribute(candidate.name)
        if info is None:
            messages.append(
                _message(
                    "vocab",
                    "vocab.unknown_attribute",
                    Severity.WARNING,
                    f"attribute {candidate.name!r} is not present in the loaded vocabulary",
                )
            )
            return VocabularyValidation(
                valid=None, allowed=None, message=f"attribute {candidate.name!r} not in vocabulary"
            )
        result = self._vocab.validate_value(candidate.name, candidate.raw_value)
        if result.valid is True:
            messages.append(
                _message(
                    "vocab",
                    "vocab.value_allowed",
                    Severity.INFO,
                    f"value {candidate.raw_value!r} is an allowed value for {candidate.name!r}",
                )
            )
        elif result.valid is False:
            messages.append(
                _message(
                    "vocab",
                    "vocab.value_not_allowed",
                    Severity.ERROR,
                    f"value {candidate.raw_value!r} is NOT an allowed value for {candidate.name!r}",
                )
            )
        else:
            messages.append(
                _message(
                    "vocab",
                    "vocab.inconclusive",
                    Severity.WARNING,
                    result.message or "vocabulary check inconclusive",
                )
            )
        return result

    def _check_uom(
        self, candidate: CandidateAttribute, messages: list[ValidationMessage]
    ) -> UomValidation:
        """UOM check; skipped when the attribute has no unit."""
        if not candidate.unit.strip():
            return UomValidation(valid=True, message="no unit present; UOM check not applicable")
        if not self._uom.is_available():
            messages.append(
                _message("uom", "uom.not_loaded", Severity.INFO, UOM_NOT_LOADED_NOTE)
            )
            return UomValidation(valid=None, message=UOM_NOT_LOADED_NOTE)
        info = self._uom.lookup_unit(candidate.unit)
        if info is None:
            messages.append(
                _message(
                    "uom",
                    "uom.unknown_unit",
                    Severity.ERROR,
                    f"unit {candidate.unit!r} is not in the loaded UOM standards",
                )
            )
            return UomValidation(valid=False, message=f"unit {candidate.unit!r} unknown")
        result = self._uom.validate_measurement_type(
            candidate.name, candidate.unit, candidate.raw_value
        )
        if result.valid is True:
            messages.append(
                _message(
                    "uom",
                    "uom.unit_verified",
                    Severity.INFO,
                    f"unit {candidate.unit!r} verified ({result.measurement_type})",
                )
            )
        elif result.valid is False:
            messages.append(
                _message(
                    "uom",
                    "uom.measurement_type_mismatch",
                    Severity.ERROR,
                    f"value does not match measurement type {result.measurement_type!r} "
                    f"for unit {candidate.unit!r}",
                )
            )
        else:
            messages.append(
                _message(
                    "uom",
                    "uom.inconclusive",
                    Severity.WARNING,
                    result.message or "UOM check inconclusive",
                )
            )
        return result


def _message(source: str, code: str, severity: Severity, text: str) -> ValidationMessage:
    return ValidationMessage(code=code, severity=severity, source=source, message=text)


def _derive_outcome(
    messages: list[ValidationMessage],
    vocab_result: VocabularyValidation,
    uom_result: UomValidation,
) -> ValidationOutcome:
    """Verdict derivation with no false VERIFIED results.

    - any ERROR            -> INVALID
    - any WARNING          -> NEEDS_REVIEW
    - all applicable checks passed against AVAILABLE resources -> VERIFIED
    - otherwise            -> NOT_VALIDATED (official data unavailable)
    """
    if any(message.severity is Severity.ERROR for message in messages):
        return ValidationOutcome.INVALID
    if any(message.severity is Severity.WARNING for message in messages):
        return ValidationOutcome.NEEDS_REVIEW
    if vocab_result.valid is True and uom_result.valid is True:
        return ValidationOutcome.VERIFIED
    return ValidationOutcome.NOT_VALIDATED


# --- mapping onto the existing domain model ----------------------------------

_OUTCOME_TO_ATTRIBUTE_STATUS = {
    ValidationOutcome.VERIFIED: AttributeStatus.VALIDATED,
    ValidationOutcome.NEEDS_REVIEW: AttributeStatus.NEEDS_REVIEW,
    ValidationOutcome.NOT_VALIDATED: AttributeStatus.EXTRACTED,
    ValidationOutcome.INVALID: AttributeStatus.REJECTED,
}

_SEVERITY_TO_VALIDATION_STATUS = {
    Severity.INFO: ValidationStatus.PASSED,
    Severity.WARNING: ValidationStatus.NEEDS_REVIEW,
    Severity.ERROR: ValidationStatus.FAILED,
}


def to_domain_attribute_value(validated: ValidatedAttribute) -> AttributeValue:
    """Map a validated attribute onto the existing domain AttributeValue.

    Preserves evidence_refs and the original raw_value; the outcome maps onto
    AttributeStatus (VERIFIED -> VALIDATED, NOT_VALIDATED -> EXTRACTED,
    NEEDS_REVIEW -> NEEDS_REVIEW, INVALID -> REJECTED).
    """
    return AttributeValue(
        name=validated.name,
        raw_value=validated.raw_value,
        value=validated.normalized_value,
        unit=validated.unit,
        confidence=validated.confidence,
        status=_OUTCOME_TO_ATTRIBUTE_STATUS[validated.outcome],
        evidence_refs=list(validated.evidence_refs),
        validation_results=to_domain_validation_results(validated),
    )


def to_domain_validation_results(validated: ValidatedAttribute) -> list[ValidationResult]:
    """Map validation messages onto the domain ValidationResult model."""
    results: list[ValidationResult] = []
    for message in validated.messages:
        status = _SEVERITY_TO_VALIDATION_STATUS[message.severity]
        if validated.outcome is ValidationOutcome.NOT_VALIDATED and message.severity is Severity.INFO:
            status = ValidationStatus.NOT_VALIDATED
        validation_type = _SOURCE_TO_VALIDATION_TYPE.get(message.source, ValidationType.RULE)
        results.append(
            ValidationResult(
                validation_type=validation_type,
                status=status,
                message=message.message,
                details={"code": message.code},
            )
        )
    return results


_SOURCE_TO_VALIDATION_TYPE = {
    "vocab": ValidationType.LOV,
    "uom": ValidationType.UOM,
}
