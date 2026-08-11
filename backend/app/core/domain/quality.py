"""Product quality metrics.

The deterministic quality-score formulas are defined by the official UniHack
rules and plug in later; until then scores default to zero and are never
fabricated. All scores are normalized to 0..1.
"""

from pydantic import BaseModel, Field


class ConfidenceSummary(BaseModel):
    count: int = 0
    min: float = Field(default=0.0, ge=0.0, le=1.0)
    max: float = Field(default=0.0, ge=0.0, le=1.0)
    mean: float = Field(default=0.0, ge=0.0, le=1.0)


class QualityScore(BaseModel):
    overall: float = Field(default=0.0, ge=0.0, le=1.0)
    evidence_coverage: float = Field(default=0.0, ge=0.0, le=1.0)
    validation_coverage: float = Field(default=0.0, ge=0.0, le=1.0)
    confidence: ConfidenceSummary = Field(default_factory=ConfidenceSummary)
