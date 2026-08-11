"""Typed request/response models for LLM operations.

These models are provider-agnostic: no vendor-specific fields appear here.
Response shapes map onto the internal domain model (app.core.domain) where
applicable and must NOT invent UniHack-specific fields.
"""

from typing import Type

from pydantic import BaseModel, Field

from app.core.domain import Classification


class LLMRequest(BaseModel):
    """Common prompt fields shared by all requests."""

    system_prompt: str = ""
    user_prompt: str = ""


class CompletionRequest(LLMRequest):
    """Free-form text completion."""

    user_prompt: str = Field(..., min_length=1)
    temperature: float | None = Field(default=None, ge=0.0, le=1.0)
    timeout_seconds: float | None = Field(default=None, gt=0.0)


class StructuredRequest(LLMRequest):
    """Base for requests whose output must match a Pydantic schema."""

    # The Pydantic model class describing the expected JSON structure.
    # The client validates provider output against it.
    output_schema: Type[BaseModel] = Field(...)
    temperature: float | None = Field(default=None, ge=0.0, le=1.0)
    timeout_seconds: float | None = Field(default=None, gt=0.0)


class StructuredCompletionRequest(StructuredRequest):
    """Generic structured completion against an arbitrary schema."""

    user_prompt: str = Field(..., min_length=1)


# --- Extraction -------------------------------------------------------------

class ExtractedAttribute(BaseModel):
    """One attribute pulled from a document, pre-normalization.

    Maps onto domain AttributeValue: name / raw_value / unit / confidence.
    Normalization happens in pipeline stages, not in the LLM layer.
    """

    name: str
    raw_value: str
    unit: str = ""
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class ExtractedAttributes(BaseModel):
    items: list[ExtractedAttribute] = Field(default_factory=list)


class ExtractionRequest(StructuredRequest):
    """Extract attributes from a document/evidence text."""

    text: str = Field(..., min_length=1)
    output_schema: Type[BaseModel] = ExtractedAttributes


# --- Classification ---------------------------------------------------------

class ClassificationRequest(StructuredRequest):
    """Classify product information into the internal Classification fields."""

    text: str = Field(..., min_length=1)
    output_schema: Type[BaseModel] = Classification


# --- Description generation -------------------------------------------------

class GeneratedDescription(BaseModel):
    text: str


class DescriptionRequest(StructuredRequest):
    """Generate one commerce-ready description variant from known attributes.

    `target` names the variant (title, short, mobile, invoice, long). The
    exact UniHack templates/limits plug in later; generation is not
    implemented yet.
    """

    target: str = "long"
    attributes: dict[str, str] = Field(default_factory=dict)
    output_schema: Type[BaseModel] = GeneratedDescription
