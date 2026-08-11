"""Normalization and validation framework (Step 5).

Modular and provider-based, READY for the official UniHack resources to be
plugged in later:

- Normalizer (generic deterministic steps only - no Unilog mappings)
- VocabularyProvider / LOVProvider (unavailable until official LOV data
  loads; never claims validity without it)
- UOMProvider (unavailable until the official UOM standards load)
- ManufacturerBrandProvider (unavailable until the official master data
  loads)
- ValidationService (structural + evidence + normalization + vocab + UOM)
- typed results: VERIFIED / NEEDS_REVIEW / NOT_VALIDATED / INVALID with
  per-message WHY explanations

Nothing here claims an attribute is officially Unilog-valid: the official
resource files (see vocab.py) are not present, so providers ship UNAVAILABLE
by default.
"""

from app.validation.lov import (
    LOV_NOT_LOADED_NOTE,
    LOVProvider,
    UnavailableLOVProvider,
    UnavailableVocabularyProvider,
    VocabularyProvider,
)
from app.validation.manufacturer_brand import (
    MASTER_DATA_NOT_LOADED_NOTE,
    ManufacturerBrandProvider,
    UnavailableManufacturerBrandProvider,
)
from app.validation.normalizer import DefaultNormalizer, Normalizer
from app.validation.service import (
    ValidationService,
    to_domain_attribute_value,
    to_domain_validation_results,
)
from app.validation.types import (
    Severity,
    ValidatedAttribute,
    ValidationMessage,
    ValidationOutcome,
    ValidationSummary,
)
from app.validation.uom import (
    UOM_NOT_LOADED_NOTE,
    UOMProvider,
    UnavailableUOMProvider,
)

__all__ = [
    "DefaultNormalizer",
    "LOV_NOT_LOADED_NOTE",
    "LOVProvider",
    "MASTER_DATA_NOT_LOADED_NOTE",
    "ManufacturerBrandProvider",
    "Normalizer",
    "Severity",
    "UOM_NOT_LOADED_NOTE",
    "UOMProvider",
    "UnavailableLOVProvider",
    "UnavailableManufacturerBrandProvider",
    "UnavailableUOMProvider",
    "UnavailableVocabularyProvider",
    "ValidatedAttribute",
    "ValidationMessage",
    "ValidationOutcome",
    "ValidationService",
    "ValidationSummary",
    "VocabularyProvider",
    "to_domain_attribute_value",
    "to_domain_validation_results",
]
