"""LOV / vocabulary provider abstraction (Step 5).

The official UniHack LOV resources (Unicat LOV, FAUCETS_LOV, Fittings_LOV,
...) are NOT available yet, so this module ships with an UNAVAILABLE
implementation that clearly reports "Official UniHack LOV data not loaded."
No LOV values are invented here.

When the official files arrive, implement a VocabularyProvider backed by
them (optionally also filling the data containers in vocab.py) and inject it
into the ValidationService - no other code needs to change.
"""

from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel, Field

LOV_NOT_LOADED_NOTE = "Official UniHack LOV data not loaded."


class AttributeInfo(BaseModel):
    """Info about an attribute known to the official vocabulary.

    Fields are filled from the official resources only; nothing is invented
    in the unavailable provider.
    """

    name: str
    allowed_values: list[str] = Field(default_factory=list)
    datatype: str = ""
    # Category/classpath context the attribute applies to, e.g. ["fittings"].
    classpaths: list[str] = Field(default_factory=list)


class VocabularyValidation(BaseModel):
    """Outcome of checking one value against the official vocabulary.

    `valid`/`allowed` are None when the check cannot be performed (official
    data not loaded or attribute not in the vocabulary) - None must never be
    treated as a pass.
    """

    valid: bool | None = None
    allowed: bool | None = None
    message: str = ""


class VocabularyProvider(Protocol):
    """Official UniHack list-of-values knowledge. `LOVProvider` is an alias.

    Replaceable: an implementation backed by the official resource files can
    be injected without redesigning the validation service.
    """

    def is_available(self) -> bool:
        """False until official LOV data is loaded."""
        ...

    def find_allowed_attribute(self, attribute_name: str) -> AttributeInfo | None:
        """Return the official attribute definition, or None if unknown."""
        ...

    def validate_value(self, attribute_name: str, value: str) -> VocabularyValidation:
        """Check a raw value against the allowed values for the attribute."""
        ...

    def normalize_value(self, attribute_name: str, value: str) -> str:
        """Normalize a value using official vocabulary rules (e.g. casing)."""
        ...

    def applicable_values_for_classpath(self, classpath: str) -> list[str]:
        """All values applicable for a category/classpath, e.g. "fittings"."""
        ...


LOVProvider = VocabularyProvider


class UnavailableVocabularyProvider:
    """Vocabulary provider that exists but has NO official data loaded.

    Every operation reports the data is missing; `valid` is always None, so
    downstream code can never mistake "not loaded" for a pass or a fail.
    """

    def is_available(self) -> bool:
        return False

    def find_allowed_attribute(self, attribute_name: str) -> AttributeInfo | None:
        return None

    def validate_value(self, attribute_name: str, value: str) -> VocabularyValidation:
        return VocabularyValidation(valid=None, allowed=None, message=LOV_NOT_LOADED_NOTE)

    def normalize_value(self, attribute_name: str, value: str) -> str:
        return value

    def applicable_values_for_classpath(self, classpath: str) -> list[str]:
        return []


UnavailableLOVProvider = UnavailableVocabularyProvider
