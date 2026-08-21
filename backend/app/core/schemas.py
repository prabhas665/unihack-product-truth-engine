"""Pydantic API request/response schemas."""

from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    status: str
    app: str
    version: str
    database_records: int | None = None


class LLMHealthResponse(BaseModel):
    """Read-only LLM connectivity status. Never contains key material."""

    provider: str
    model: str
    fallback_models: list[str] = Field(default_factory=list)
    key_configured: bool
    chat_completions_status: int | None = None
    error: str = ""
    elapsed_ms: int = 0


class LookupRequest(BaseModel):
    """Quick lookup: manufacturer + part number, optionally brand/description."""

    manufacturer: str = Field(..., min_length=1)
    part_number: str = Field(..., min_length=1)
    brand: str = ""
    description: str = ""
