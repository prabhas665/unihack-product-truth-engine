"""UOM provider abstraction (Step 5).

The official "Unilog Master UOM Standards" resource is NOT available yet, so
this module ships with an UNAVAILABLE implementation that clearly reports
"Official UniHack UOM data not loaded." No approved UOM abbreviations are
invented here.

When the official standards arrive, implement a UOMProvider backed by them
and inject it into the ValidationService - no other code needs to change.
"""

from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel

UOM_NOT_LOADED_NOTE = "Official UniHack UOM data not loaded."


class UnitInfo(BaseModel):
    """Info about one unit of measure, filled from official data only.

    `factor` (e.g. conversion factor to the canonical unit) comes from the
    official standards when they arrive; nothing is invented here.
    """

    unit: str
    canonical_unit: str = ""
    measurement_type: str = ""
    factor: float | None = None


class UomValidation(BaseModel):
    """Outcome of one UOM check.

    `valid` is None when the check cannot be performed (official UOM data
    not loaded) - None must never be treated as a pass.
    """

    valid: bool | None = None
    measurement_type: str = ""
    message: str = ""


class UOMProvider(Protocol):
    """Unit-of-measure knowledge. Replaceable via official data later."""

    def is_available(self) -> bool:
        """False until official UOM standards are loaded."""
        ...

    def lookup_unit(self, unit: str) -> UnitInfo | None:
        """Resolve a unit token to its official definition, or None."""
        ...

    def normalize_unit(self, unit: str, attribute_name: str = "") -> str:
        """Normalize a unit token to its canonical official form."""
        ...

    def validate_measurement_type(
        self, attribute_name: str, unit: str, value: str
    ) -> UomValidation:
        """Check that `value` matches the measurement type of `unit`."""
        ...


class UnavailableUOMProvider:
    """UOM provider that exists but has NO official data loaded.

    Every operation reports the data is missing; `valid` is always None, so
    downstream code can never mistake "not loaded" for a pass or a fail.
    """

    def is_available(self) -> bool:
        return False

    def lookup_unit(self, unit: str) -> UnitInfo | None:
        return None

    def normalize_unit(self, unit: str, attribute_name: str = "") -> str:
        return unit

    def validate_measurement_type(
        self, attribute_name: str, unit: str, value: str
    ) -> UomValidation:
        return UomValidation(valid=None, message=UOM_NOT_LOADED_NOTE)
