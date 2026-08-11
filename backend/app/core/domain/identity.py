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
