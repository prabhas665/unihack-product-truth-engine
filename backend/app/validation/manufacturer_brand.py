"""Manufacturer/brand provider abstraction (Step 5).

The official UniCat Manufacturer/Brand List is NOT available yet, so this
module ships with an UNAVAILABLE implementation that clearly reports the
master data is not loaded. No manufacturer/brand master data is invented
here.

When the official list arrives, implement a ManufacturerBrandProvider backed
by it and inject it where identity checks run - no other code needs to
change.
"""

from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel, Field

MASTER_DATA_NOT_LOADED_NOTE = (
    "Official UniHack manufacturer/brand master data not loaded."
)


class ManufacturerMatch(BaseModel):
    """Result of matching a raw manufacturer string against official data."""

    matched: bool = False
    canonical_manufacturer: str = ""
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    message: str = ""


class BrandMatch(BaseModel):
    """Result of matching a raw brand string against official data."""

    matched: bool = False
    canonical_brand: str = ""
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    message: str = ""


class ManufacturerBrandProvider(Protocol):
    """Official manufacturer/brand master data. Replaceable later."""

    def is_available(self) -> bool:
        """False until the official master data is loaded."""
        ...

    def match_manufacturer(self, value: str) -> ManufacturerMatch:
        """Match a raw manufacturer string to the canonical manufacturer."""
        ...

    def canonical_manufacturer(self, manufacturer: str) -> str:
        """Return the canonical name for a manufacturer."""
        ...

    def match_brand(self, value: str) -> BrandMatch:
        """Match a raw brand string to the canonical brand."""
        ...

    def canonical_brand(self, brand: str) -> str:
        """Return the canonical name for a brand."""
        ...


class UnavailableManufacturerBrandProvider:
    """Master-data provider that exists but has NO official data loaded.

    Matches always return `matched=False` with a clear explanation, so
    downstream code can never mistake "not loaded" for a successful match.
    """

    def is_available(self) -> bool:
        return False

    def match_manufacturer(self, value: str) -> ManufacturerMatch:
        return ManufacturerMatch(matched=False, message=MASTER_DATA_NOT_LOADED_NOTE)

    def canonical_manufacturer(self, manufacturer: str) -> str:
        return manufacturer

    def match_brand(self, value: str) -> BrandMatch:
        return BrandMatch(matched=False, message=MASTER_DATA_NOT_LOADED_NOTE)

    def canonical_brand(self, brand: str) -> str:
        return brand
