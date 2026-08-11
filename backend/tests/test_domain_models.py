"""Unit tests for the internal domain model (Step 2A).

These tests validate the model's structure, constraints, and reference
integrity. No AI, scraping, or enrichment is involved.
"""

import pytest
from pydantic import ValidationError

from app.core.domain import (
    AttributeValue,
    CandidateValue,
    ConflictStatus,
    Evidence,
    ProcessingMetadata,
    ProcessingStatus,
    ProductIdentity,
    ProductIntelligence,
    ReviewDecision,
    ReviewState,
    SourceTrustLevel,
    SourceType,
    ValidationResult,
    ValidationStatus,
    ValidationType,
)


def make_evidence(evidence_id: str = "ev-1") -> Evidence:
    return Evidence(
        id=evidence_id,
        source_url="https://example.com/manufacturer/part/123",
        source_type=SourceType.MANUFACTURER_PRODUCT_PAGE,
        source_title="Manufacturer Product Page",
        snippet="Length 100 mm",
        trust_level=SourceTrustLevel.MANUFACTURER_OFFICIAL,
        supports_attributes=["length"],
    )


class TestProductIdentity:
    def test_partial_messy_input_is_accepted(self):
        """Batch input is dirty; identity must tolerate missing fields."""
        identity = ProductIdentity(mpn="ABC-123")
        assert identity.manufacturer == ""
        assert identity.brand == ""
        assert identity.raw_description == ""
        assert identity.sku is None

    def test_full_identity_round_trip(self):
        identity = ProductIdentity(
            manufacturer="Acme Corp",
            brand="Acme Pro",
            mpn="ABC-123",
            raw_description="A widget",
            sku="S-001",
        )
        restored = ProductIdentity.model_validate(identity.model_dump())
        assert restored == identity
        assert restored.mpn == "ABC-123"


class TestClassification:
    def test_class_alias(self):
        classification = ProductIntelligence().classification.__class__(
            department="Electric", class_="Motors", classpath="A > B"
        )
        assert classification.department == "Electric"

    def test_accepts_delivery_style_class_key(self):
        from app.core.domain import Classification

        classification = Classification.model_validate(
            {"department": "Electric", "class": "Motors"}
        )
        assert classification.class_ == "Motors"


class TestAttributeValue:
    def test_confidence_must_be_within_range(self):
        with pytest.raises(ValidationError):
            AttributeValue(name="length", confidence=1.5)
        with pytest.raises(ValidationError):
            AttributeValue(name="length", confidence=-0.1)

    def test_multiple_candidates_can_represent_conflict(self):
        attribute = AttributeValue(
            name="length",
            raw_value="100 mm",
            candidates=[
                CandidateValue(value="100 mm", evidence_refs=["ev-1"]),
                CandidateValue(value="150 mm", evidence_refs=["ev-2"]),
            ],
            conflict_status=ConflictStatus.CONFLICT,
        )
        assert attribute.conflict_status == ConflictStatus.CONFLICT
        assert len(attribute.candidates) == 2


class TestEvidence:
    def test_defaults(self):
        evidence = Evidence(
            id="ev-1", source_url="https://x.dev", source_type=SourceType.MANUFACTURER_MANUAL
        )
        assert evidence.trust_level == SourceTrustLevel.UNVERIFIED
        assert evidence.snippet == ""
        assert evidence.supports_attributes == []

    def test_id_and_url_required(self):
        with pytest.raises(ValidationError):
            Evidence(id="ev-1", source_type=SourceType.MANUFACTURER_PRODUCT_PAGE)
        with pytest.raises(ValidationError):
            Evidence(source_url="https://x.dev", source_type=SourceType.MANUFACTURER_PRODUCT_PAGE)


class TestValidationResult:
    def test_validation_result(self):
        result = ValidationResult(
            validation_type=ValidationType.LOV,
            status=ValidationStatus.PASSED,
            details={"lov_key": "length"},
        )
        assert result.validation_type == ValidationType.LOV
        assert result.status == ValidationStatus.PASSED
        assert result.details["lov_key"] == "length"


class TestReviewState:
    def test_review_state_holds_decision(self):
        review = ReviewState(
            needs_review=True,
            reason="Conflicting length values",
            decision=ReviewDecision.APPROVED,
            reviewer_notes="Verified against datasheet",
        )
        assert review.decision == ReviewDecision.APPROVED
        assert review.reviewer_notes


class TestProductIntelligence:
    def test_aggregate_defaults(self):
        product = ProductIntelligence()
        assert product.processing.status == ProcessingStatus.PENDING
        assert product.quality.overall == 0.0
        assert product.attributes == {}

    def test_evidence_references_must_resolve(self):
        with pytest.raises(ValidationError, match="unknown evidence ids"):
            ProductIntelligence(
                attributes={
                    "length": AttributeValue(
                        name="length", evidence_refs=["ev-missing"]
                    )
                }
            )

    def test_evidence_references_resolve_when_evidence_present(self):
        product = ProductIntelligence(
            evidence={"ev-1": make_evidence()},
            attributes={
                "length": AttributeValue(
                    name="length", raw_value="100 mm", evidence_refs=["ev-1"]
                )
            },
        )
        assert product.attributes["length"].evidence_refs == ["ev-1"]

    def test_candidate_references_must_resolve(self):
        with pytest.raises(ValidationError, match="unknown evidence ids"):
            ProductIntelligence(
                attributes={
                    "length": AttributeValue(
                        name="length",
                        candidates=[
                            CandidateValue(value="100 mm", evidence_refs=["ev-x"])
                        ],
                    )
                }
            )

    def test_json_round_trip_preserves_structure(self):
        product = ProductIntelligence(
            identity=ProductIdentity(manufacturer="Acme Corp", mpn="ABC-123"),
            evidence={"ev-1": make_evidence()},
            attributes={
                "length": AttributeValue(
                    name="length",
                    raw_value="100 mm",
                    value="100",
                    unit="mm",
                    evidence_refs=["ev-1"],
                )
            },
            processing=ProcessingMetadata(status=ProcessingStatus.COMPLETED),
        )
        restored = ProductIntelligence.model_validate(product.model_dump())
        assert restored == product
        assert restored.evidence["ev-1"].source_url == make_evidence().source_url
