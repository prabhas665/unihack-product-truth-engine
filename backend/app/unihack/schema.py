"""Delivery-format schema, loaded from the official reference file (Step 6A).

The 252-column header is NEVER typed out by hand: ``DeliverySchema`` is built
from the official reference CSV at runtime and validated (count, uniqueness,
no blank headers). Constants exist only for the columns the mapper actually
touches, keyed by the exact official header names.
"""

from collections import OrderedDict
from pathlib import Path

from app.unihack.delivery_headers import (
    ARTIFACT_VERSION,
    COLUMN_COUNT as FROZEN_COLUMN_COUNT,
    DELIVERY_HEADERS,
    GENERATED_AT,
    SOURCE_FILENAME,
    SOURCE_SHA256,
)

EXPECTED_COLUMN_COUNT: int = 252
ATTRIBUTE_SLOT_COUNT: int = 50

# Delivery column names the mapper knows about. 1-based column numbers.
COLUMN_NUMBERS: "OrderedDict[str, int]" = OrderedDict(
    {
        "MFR URL": 1,
        "Ref URL 1": 2,
        "Ref URL 2": 3,
        "Ref URL 3": 4,
        "Ref URL 4": 5,
        "Ref URL 5": 6,
        "PART_NUMBER": 7,
        "Dept": 8,
        "Class": 9,
        "Fine": 10,
        "SKU - MY_PART_NUMBER": 11,
        "Mfg_Part_Num": 12,
        "Part_Desc": 13,
        "E1_Brand": 14,
        "Unilog_Brand": 15,
        "DIB_Brand": 16,
        "Part_Manuf": 17,
        "MANUFACTURER_NAME": 18,
        "BRAND_NAME": 19,
        "Classpath": 23,
        "MOBILE_DESC": 24,
        "INVOICE_DESC": 25,
        "SHORT_DESC": 26,
        "LONG_DESC1": 27,
        "RETAIL_DESC": 28,
        "MARKETING_DESCRIPTION": 29,
        "With": 50,
        "Application": 53,
        "Includes": 54,
        "Product Name": 55,
        "Product Image": 225,
        "Alternate Image 1": 226,
        "Alternate Image 2": 227,
        "Alternate Image 3": 228,
        "Alternate Image 4": 229,
        "SDS": 230,
        "SDS_1": 231,
        "Warranty Information": 232,
        "Catalog": 233,
        "Specification Sheet": 234,
        "Instruction/Installation Manual": 235,
        "Service Manual": 236,
        "Owners/User Manual": 237,
        "Line Drawing": 238,
        "MTR": 239,
        "RoHS": 240,
        "Full Engineering Drawing": 241,
        "Energy Star Guide": 242,
        "Technical Bulletin": 243,
        "Submittal": 244,
        "Compatibility Chart": 245,
        "Size Chart": 246,
        "Product Label/Insert": 247,
        "Video Link": 248,
        "Video Link 1": 249,
    }
)

ATTRIBUTE_SLOT_START_COLUMN: int = 56  # ATTRIBUTE_LABEL 1

# Columns that must stay blank (with a note) until a future enrichment stage
# produces verified values; the mapper never invents them.
AMBIGUOUS_COLUMNS: list[str] = [
    "PART_NUMBER",
    "SKU - MY_PART_NUMBER",
    "MANUFACTURER_NAME",
    "BRAND_NAME",
    "Dept",
    "Class",
    "Fine",
    "Classpath",
]


class SchemaError(ValueError):
    """The delivery schema is missing, malformed, or inconsistent."""


class DeliverySchema:
    """The validated 252-column header of the official delivery format."""

    def __init__(self, headers: list[str]):
        if not headers:
            raise SchemaError("delivery schema is empty")
        if len(headers) != EXPECTED_COLUMN_COUNT:
            raise SchemaError(
                f"expected exactly {EXPECTED_COLUMN_COUNT} delivery columns, "
                f"got {len(headers)}"
            )
        blank = [i + 1 for i, name in enumerate(headers) if name.strip() == ""]
        if blank:
            raise SchemaError(
                f"blank delivery header at column(s): {blank}"
            )
        duplicates = [
            name
            for name, count in {h: headers.count(h) for h in headers}.items()
            if count > 1
        ]
        if duplicates:
            raise SchemaError(
                f"duplicate delivery headers: {duplicates}"
            )
        self._headers: list[str] = list(headers)
        self._index: dict[str, int] = {
            name: i for i, name in enumerate(self._headers)
        }

    @classmethod
    def frozen(cls) -> "DeliverySchema":
        """Load the schema from the committed, versioned frozen artifact.

        This is the ONLY schema source used by production code. It performs no
        file I/O and has no dependency on the official reference CSV at runtime.
        """
        return cls(list(DELIVERY_HEADERS))

    @classmethod
    def from_reference_csv(cls, path: str | Path) -> "DeliverySchema":
        """Load and validate the schema from a delivery-format CSV header.

        Retained for development/evaluation tooling (it reads the dev fixture
        copy, never a production artifact). Production routing must use
        :meth:`frozen` instead.
        """
        import csv

        file_path = Path(path)
        if not file_path.is_file():
            raise SchemaError(f"delivery reference file not found: {file_path}")
        with file_path.open(
            "r", encoding="utf-8-sig", newline=""
        ) as handle:
            reader = csv.reader(handle)
            try:
                header = next(reader)
            except StopIteration:
                raise SchemaError(
                    f"delivery reference file is empty: {file_path}"
                ) from None
        return cls(header)

    @property
    def headers(self) -> list[str]:
        return list(self._headers)

    @property
    def count(self) -> int:
        return len(self._headers)

    def column_exists(self, name: str) -> bool:
        return name in self._index

    def index_of(self, name: str) -> int:
        """0-based index of the named column (raises KeyError if unknown)."""
        return self._index[name]

    def label_at(self, index: int) -> str:
        return self._headers[index]

    def attribute_slots(self) -> list[tuple[str, str, str]]:
        """(label, value, uom) header names for the 50 attribute triples."""
        slots: list[tuple[str, str, str]] = []
        for number in range(1, ATTRIBUTE_SLOT_COUNT + 1):
            slots.append(
                (
                    f"ATTRIBUTE_LABEL {number}",
                    f"ATTRIBUTE_VALUE {number}",
                    f"ATTRIBUTE_UOM {number}",
                )
            )
        return slots

    def __len__(self) -> int:
        return self.count

    def __iter__(self):
        return iter(self._headers)
