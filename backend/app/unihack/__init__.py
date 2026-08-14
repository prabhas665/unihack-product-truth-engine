"""UniHack delivery integration layer (Step 6A).

Maps the internal ProductIntelligence model into the 252-column UniHack
delivery format and writes delivery CSV. The 252-column header is frozen in a
committed artifact (``app.unihack.delivery_headers``) and loaded via
``DeliverySchema.frozen`` - production routing no longer depends on the
official reference CSV at runtime. Stdlib only; no network.
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
from app.unihack.paths import repo_root
from app.unihack.schema import (
    EXPECTED_COLUMN_COUNT,
    DELIVERY_HEADERS,
    DeliverySchema,
    SchemaError,
)
from app.unihack.writer import DeliveryCsvWriter, DeliveryWriteError

__all__ = [
    "DELIVERY_HEADERS",
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
    "repo_root",
]
