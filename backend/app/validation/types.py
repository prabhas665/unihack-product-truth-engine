"""Typed models for the normalization and validation framework (Step 5).

This framework is intentionally OFFICIAL-DATA-INDEPENDENT: it validates
structure, evidence references, and - only when official resources are
loaded - vocabulary (LOV), UOM, and manufacturer/brand rules. The official
UniHack LOV values, UOM standards, and master data are NOT available yet,
so nothing here can produce a VERIFIED verdict until a provider backed by
real data is plugged in.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class ValidationOutcome(str, Enum):
    """Overall verdict for one candidate attribute after validation.

    VERIFIED     - every applicable check passed against an AVAILABLE
                   official resource (never produced while the official
                   data is unavailable).
    NEEDS_REVIEW - no hard errors, but something needs human attention
                   (conflicting candidates, inconclusive checks, low
                   confidence, attribute unknown to the loaded vocabulary).
    NOT_VALIDATED - a.k.a. UNKNOWN: could not be checked because official
                   data is not loaded; nothing was proven, nothing failed.
    INVALID      - structural or evidence errors: the value cannot be used.
    """

    VERIFIED = "verified"
    NEEDS_REVIEW = "needs_review"
    NOT_VALIDATED = "not_validated"
    INVALID = "invalid"


class Severity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class ValidationMessage(BaseModel):
    """One message explaining WHY a value was accepted / rejected / skipped.

    `code` is a stable machine-readable identifier (e.g.
    "evidence.dangling_references"); `source` names the component that
    produced it ("structural", "evidence", "vocab", "uom",
    "normalization").
    """

    code: str
    severity: Severity
    source: str
    message: str


class ValidatedAttribute(BaseModel):
    """A candidate attribute after normalization and validation.

    Invariants:
    - `raw_value` is ALWAYS the original AI candidate value (never replaced).
    - `normalized_value` is the deterministic normalization result; it may
      differ from raw_value but never overwrites it.
    - `evidence_refs` are preserved unchanged from extraction.
    """

    name: str
    raw_value: str = ""
    normalized_value: str = ""
    unit: str = ""
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    evidence_refs: list[str] = Field(default_factory=list)
    outcome: ValidationOutcome = ValidationOutcome.NOT_VALIDATED
    messages: list[ValidationMessage] = Field(default_factory=list)
    # Ordered list of normalization steps actually applied, e.g.
    # ["text.cleanup", "fraction_decimal.convert"].
    normalization_applied: list[str] = Field(default_factory=list)


class ValidationSummary(BaseModel):
    """Result of validating one batch of candidate attributes."""

    attributes: list[ValidatedAttribute] = Field(default_factory=list)
    # ValidationOutcome value -> number of attributes with that outcome.
    counts: dict[str, int] = Field(default_factory=dict)
