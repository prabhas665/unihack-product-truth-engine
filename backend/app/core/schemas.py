"""Pydantic API request/response schemas."""

from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    status: str
    app: str
    version: str


class LookupRequest(BaseModel):
    """Quick lookup: manufacturer + part number, optionally brand/description."""

    manufacturer: str = Field(..., min_length=1)
    part_number: str = Field(..., min_length=1)
    brand: str = ""
    description: str = ""
