"""Official UniHack controlled vocabularies and UOM rules (data containers).

These containers are intentionally EMPTY. The official UniHack LOV values and
UOM rules are not available yet and must NOT be invented. When the official
resources are released, load them here (from files/URLs) or fill these
containers from them. Never hard-code guessed values.

The validation framework (see lov.py / uom.py / manufacturer_brand.py /
service.py in this package) reads official data through replaceable
providers; those providers can source their data from these containers or
from the raw resource files directly.
"""

# Canonical manufacturer name mapping: alias -> canonical.
MANUFACTURER_ALIASES: dict[str, str] = {}

# Canonical brand name mapping: alias -> canonical.
BRAND_ALIASES: dict[str, str] = {}

# List of Values validation: attribute key -> set of permitted values.
LOV: dict[str, set[str]] = {}

# UOM normalization: unit token -> canonical UOM (e.g. "mm" -> "millimetre").
UOM_ALIASES: dict[str, str] = {}

# Target UOM per dimensional attribute, e.g. length always normalizes to mm.
DEFAULT_UOMS: dict[str, str] = {}
