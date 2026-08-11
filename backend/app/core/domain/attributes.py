"""Product attributes with multi-candidate conflict support.

Each attribute carries its own candidate values gathered from evidence. The
conflict_status represents agreement / conflict / unresolved; the resolution
logic itself is a future pipeline stage, not part of the model.
"""

from pydantic import BaseModel, Field

from app.core.domain.enums import (
    AttributeStatus,
    ConflictStatus,
    SourceTrustLevel,
)
from app.core.domain.review import ReviewState
from app.core.domain.validation import ValidationResult


class CandidateValue(BaseModel):
    """One source-derived candidate value for an attribute (pre-resolution)."""

    value: str
    normalized_value: str = ""
    unit: str = ""
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    evidence_refs: list[str] = Field(default_factory=list)
    source_trust_level: SourceTrustLevel = SourceTrustLevel.UNVERIFIED


class AttributeValue(BaseModel):
    """One attribute of a product, with evidence, validation, and review info."""

    name: str
    # raw_value: value as extracted from evidence.
    raw_value: str = ""
    # value: final normalized value after the pipeline has run.
    value: str = ""
    unit: str = ""
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    status: AttributeStatus = AttributeStatus.EXTRACTED
    # Evidence ids (see Evidence.id) backing this attribute.
    evidence_refs: list[str] = Field(default_factory=list)
    validation_results: list[ValidationResult] = Field(default_factory=list)
    # All candidate values seen; conflict_status summarizes their agreement.
    candidates: list[CandidateValue] = Field(default_factory=list)
    conflict_status: ConflictStatus = ConflictStatus.AGREEMENT
    review: ReviewState | None = None
