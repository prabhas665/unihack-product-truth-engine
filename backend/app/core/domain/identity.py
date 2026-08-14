"""Product identity: the minimal, often messy, input we start from.

All fields tolerate empty values on purpose: real batch input is partial and
dirty. Missing identity fields are data-quality issues that later pipeline
stages (or the official UniHack rules) flag - not schema errors.
"""

from pydantic import BaseModel, Field


class ProductIdentity(BaseModel):
    manufacturer: str = ""
    brand: str = ""
    # Manufacturer Part Number.
    mpn: str = Field(default="", description="Manufacturer Part Number")
    raw_description: str = ""
    sku: str | None = None

    # Verified (non-input) identity, filled only from trusted sources
    # (seed table / live provider). Never a placeholder or raw input token.
    verified_manufacturer: str = ""
    verified_brand: str = ""
    verified_trade_name: str = ""
    # Provenance trace, e.g. "mpn", "brand", "manufacturer".
    identity_provenance: str = ""
