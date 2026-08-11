"""Normalizer abstraction (Step 5).

What is implemented NOW is generic and deterministic only:
- text cleanup (trim, collapse internal whitespace) - universal and safe
- fraction -> decimal conversion (pure math via fractions.Fraction)

Everything that depends on official UniHack knowledge is intentionally NOT
implemented here: manufacturer/brand aliasing, normalization to canonical
official UOMs, and category-specific normalization. Those require the
official resource files (UniCat Manufacturer/Brand List, Unilog Master UOM
Standards, Decimal_Fraction.xlsx, FAUCETS_LOV, Fittings_LOV, ...) and will
be implemented by adapters backed by that data. No Unilog mapping is
invented.
"""

from __future__ import annotations

import re
from fractions import Fraction
from typing import Protocol

from app.validation.uom import UOMProvider, UnavailableUOMProvider

_WHITESPACE = re.compile(r"\s+")


class Normalizer(Protocol):
    """Normalization operations the pipeline can rely on.

    Replaceable: adapters may layer official Unilog mappings on top once the
    resource files arrive, without redesigning the validation service.
    """

    def normalize_text(self, value: str) -> str:
        """Universal text cleanup: trim + collapse whitespace."""
        ...

    def normalize_manufacturer(self, value: str) -> str:
        """Canonical manufacturer name (needs official master data)."""
        ...

    def normalize_brand(self, value: str) -> str:
        """Canonical brand name (needs official master data)."""
        ...

    def normalize_unit(self, value: str, attribute_name: str = "") -> str:
        """Canonical unit (needs official UOM standards)."""
        ...

    def normalize_fraction_decimal(self, value: str) -> str:
        """Generic fraction -> decimal conversion, e.g. '3/4' -> '0.75'."""
        ...

    def normalize_category_specific(self, attribute_name: str, value: str) -> str:
        """Category-specific normalization (needs official LOV rules)."""
        ...


class DefaultNormalizer:
    """Generic deterministic normalizer. NO official Unilog mappings.

    manufacturer/brand/category normalization only clean text and otherwise
    return the input unchanged until official resources are available -
    see the note on each method.
    """

    def __init__(self, uom_provider: UOMProvider | None = None) -> None:
        self._uom_provider = uom_provider or UnavailableUOMProvider()

    def normalize_text(self, value: str) -> str:
        return _WHITESPACE.sub(" ", value.strip())

    def normalize_manufacturer(self, value: str) -> str:
        # Requires the official UniCat Manufacturer/Brand List. Without it we
        # only clean text - we never alias or canonicalize manufacturer names.
        return self.normalize_text(value)

    def normalize_brand(self, value: str) -> str:
        # Same as manufacturer: requires official master data for aliasing.
        return self.normalize_text(value)

    def normalize_unit(self, value: str, attribute_name: str = "") -> str:
        # Delegates to the UOM provider; with no official data loaded this
        # returns the cleaned input unchanged (no approved abbreviations
        # are invented).
        cleaned = self.normalize_text(value)
        if not self._uom_provider.is_available():
            return cleaned
        return self._uom_provider.normalize_unit(cleaned, attribute_name=attribute_name)

    def normalize_fraction_decimal(self, value: str) -> str:
        # Pure generic math on integer fractions only, e.g. "3/4" -> "0.75",
        # "8/4" -> "2". Non-numeric tokens are left untouched.
        value = self.normalize_text(value)
        parts = value.split("/")
        if len(parts) != 2:
            return value
        try:
            fraction = Fraction(int(parts[0].strip()), int(parts[1].strip()))
        except (ValueError, ZeroDivisionError):
            return value
        if fraction.denominator == 1:
            return str(fraction.numerator)
        return f"{fraction.numerator / fraction.denominator:g}"

    def normalize_category_specific(self, attribute_name: str, value: str) -> str:
        # Requires official category rules (e.g. FAUCETS_LOV / Fittings_LOV);
        # not implemented until those resources exist.
        return self.normalize_text(value)
