"""UniHack dataset integration layer (Step 6A).

Parses the official UniHack input CSV, loads the official 252-column delivery
schema from its reference file, maps the internal ProductIntelligence model
into delivery rows, and writes delivery CSV. Stdlib only; no network.
"""

from app.unihack.mapper import UniHackDeliveryMapper
from app.unihack.models import (
    DeliveryRow,
    UniHackInputResult,
    UniHackInputRow,
    UniHackRowError,
)
from app.unihack.parser import (
    EXPECTED_INPUT_COLUMN_COUNT,
    INPUT_COLUMNS,
    PLACEHOLDER_DIB_BRAND,
    PLACEHOLDER_E1_BRAND,
    PLACEHOLDER_PART_MANUF,
    PLACEHOLDER_UNILOG_BRAND,
    REQUIRED_FIELDS,
    UniHackInputError,
    UniHackInputParser,
)
from app.unihack.paths import (
    delivery_reference_path,
    repo_root,
    unihack_input_path,
)
from app.unihack.schema import (
    EXPECTED_COLUMN_COUNT,
    SchemaError,
    DeliverySchema,
)
from app.unihack.writer import DeliveryCsvWriter, DeliveryWriteError

__all__ = [
    "DeliveryCsvWriter",
    "DeliveryRow",
    "DeliverySchema",
    "DeliveryWriteError",
    "EXPECTED_COLUMN_COUNT",
    "EXPECTED_INPUT_COLUMN_COUNT",
    "INPUT_COLUMNS",
    "PLACEHOLDER_DIB_BRAND",
    "PLACEHOLDER_E1_BRAND",
    "PLACEHOLDER_PART_MANUF",
    "PLACEHOLDER_UNILOG_BRAND",
    "REQUIRED_FIELDS",
    "SchemaError",
    "UniHackDeliveryMapper",
    "UniHackInputError",
    "UniHackInputParser",
    "UniHackInputResult",
    "UniHackInputRow",
    "UniHackRowError",
    "delivery_reference_path",
    "repo_root",
    "unihack_input_path",
]
