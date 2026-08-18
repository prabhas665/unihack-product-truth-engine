"""Typed models for evidence-based attribute extraction.

This layer sits between the LLM provider (app.llm, provider-agnostic) and
the domain model (app.core.domain). It turns supplied EvidenceRecords into
candidate attributes, each traceable to its evidence ids.

Official UniHack LOV/UOM validation is a FUTURE stage and is deliberately
not performed here.
"""

from __future__ import annotations

from enum import Enum
from typing import Annotated

from pydantic import BaseModel, BeforeValidator, Field

from app.core.domain import ProductIdentity
from app.sources.retrieval import EvidenceRecord


def _reject_bool_confidence(value: object) -> object:
    """Reject bool confidence early so True/False never coerce to 1.0/0.0.

    Pydantic would otherwise coerce a boolean into the float field in lax
    mode; a boolean carries no numeric meaning here, so the item must fail
    schema validation and be rejected per attribute.
    """
    if isinstance(value, bool):
        raise ValueError("confidence must be a number 0..1")
    return value


class ExtractionErrorKind(str, Enum):
    SCHEMA_INVALID = "schema_invalid"  # malformed JSON or schema violations
    LLM_FAILED = "llm_failed"  # provider unavailable/timeout/etc.


class ExtractionError(Exception):
    """Typed failure of the extraction service.

    Raised when the LLM output cannot be used at all (malformed or schema-
    invalid) or when the LLM call itself failed. Never leaks raw model
    output.
    """

    def __init__(self, kind: ExtractionErrorKind, message: str) -> None:
        super().__init__(message)
        self.kind = kind
        self.message = message


class ExtractionRequest(BaseModel):
    """What the extraction service needs: identity + supplied evidence."""

    identity: ProductIdentity = Field(default_factory=ProductIdentity)
    raw_description: str = ""
    # One or more retrieved evidence records (see app.sources.retrieval).
    evidence_records: list[EvidenceRecord] = Field(..., min_length=1)


class ExtractionOutputItem(BaseModel):
    """One attribute claim as returned by the LLM (AI-facing JSON schema)."""

    name: str
    raw_value: str = ""
    normalized_value: str = ""  # only when obvious and evidence-supported
    unit: str = ""
    confidence: Annotated[
        float, BeforeValidator(_reject_bool_confidence)
    ] = Field(default=0.0, ge=0.0, le=1.0)
    evidence_ids: list[str] = Field(default_factory=list)
    notes: str = ""


class ExtractionOutput(BaseModel):
    """The structured output schema the LLM must conform to."""

    items: list[ExtractionOutputItem] = Field(default_factory=list)


class CandidateAttribute(BaseModel):
    """One accepted candidate value, bound to evidence ids.

    evidence_ids is always non-empty: every extracted attribute must be
    traceable to the supplied evidence.
    """

    name: str
    raw_value: str
    normalized_value: str = ""
    unit: str = ""
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    evidence_ids: list[str] = Field(..., min_length=1)
    # Concise, evidence-based, user-safe note (never chain-of-thought).
    notes: str = ""
    # Exact short excerpt of the supporting evidence text (Step 8B),
    # resolved deterministically from the retrieved record and anchored to
    # the requested product's own passage (P0 claim-support gate). Non-empty
    # for every accepted attribute: a claim without a supported occurrence
    # is rejected ("claim not found in cited evidence") instead of being
    # accepted with an empty quote.
    quote: str = ""


class RejectedAttribute(BaseModel):
    """A claim the LLM made that we refused, with the reason why."""

    name: str
    raw_value: str = ""
    reason: str


class ExtractionResponse(BaseModel):
    """Result of one extraction run.

    `attributes` contains only claim-supported candidates (P0 gate: the
    value must occur deterministically in the cited evidence, in the
    requested product's own passage or unattributable family copy);
    everything else the LLM claimed lands in `rejected` with a reason.
    Conflicts are NOT resolved - multiple candidates with the same name are
    allowed and flagged later by the validation stage.
    """

    attributes: list[CandidateAttribute] = Field(default_factory=list)
    rejected: list[RejectedAttribute] = Field(default_factory=list)
    evidence_ids_used: list[str] = Field(default_factory=list)
