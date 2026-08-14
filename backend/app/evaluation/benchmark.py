"""Benchmark loading + expected-value normalization (Step 14B, D3).

Loads the official input + expected-output CSVs and exposes the sampled cells
we can score against. The expected-output sample carries dirty brand artifacts
(``FRIGIDAIREAr``, ``Whirlpoolr``) that our verified output intentionally does
NOT replicate, so the comparator whitelist-normalizes those cells before
scoring (see ``normalize_expected_cell``).
"""

from __future__ import annotations

import csv

from app.identity.mapping import is_placeholder
from app.unihack.parser import (
    PLACEHOLDER_DIB_BRAND,
    PLACEHOLDER_E1_BRAND,
    PLACEHOLDER_PART_MANUF,
    PLACEHOLDER_UNILOG_BRAND,
    UniHackInputParser,
)

# The six input columns are passed through verbatim; placeholder tokens there
# are expected (the official format keeps them). Leak detection only applies to
# the 246 derived columns.
PASSTHROUGH_COLUMNS = {
    "Mfg_Part_Num",
    "Part_Desc",
    "E1_Brand",
    "Unilog_Brand",
    "DIB_Brand",
    "Part_Manuf",
}
LEAK_TOKENS = {
    PLACEHOLDER_E1_BRAND,
    PLACEHOLDER_UNILOG_BRAND,
    PLACEHOLDER_DIB_BRAND,
    PLACEHOLDER_PART_MANUF,
    "COMMODITY - UNBRANDED",
    "COMMODITY-UNBRANDED",
}

SAMPLED_IDENTITY_COLUMNS = [
    "Mfg_Part_Num",
    "MANUFACTURER_NAME",
    "BRAND_NAME",
    "TRADE_NAME",
]
SAMPLED_DESC_COLUMNS = [
    "MOBILE_DESC",
    "INVOICE_DESC",
    "SHORT_DESC",
    "LONG_DESC1",
    "RETAIL_DESC",
    "MARKETING_DESCRIPTION",
]


def load_input_rows(path: str) -> list:
    """Parse the official input CSV into UniHackInputRow objects."""
    return UniHackInputParser().parse_path(path).rows


def load_expected(path: str) -> dict[str, dict]:
    """Load expected-output rows keyed by Mfg_Part_Num (stripped)."""
    expected: dict[str, dict] = {}
    with open(path, encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            mpn = (row.get("Mfg_Part_Num") or "").strip()
            if mpn:
                expected[mpn] = row
    return expected


def clean_dirty_brand(value: str) -> str:
    """Strip the dirty trailing artifacts present in the sample expected data
    (``FRIGIDAIRE\\u00ae`` -> ``FRIGIDAIRE``, ``Whirlpoolr`` -> ``Whirlpool``).

    The official sample carries non-ASCII mojibake (e.g. the registered-sign
    artifact) and trailing ``r``/``Ar`` noise; our verified output is canonical
    ASCII, so the comparator removes any non-ASCII character and a trailing
    ``r``/``Ar`` before scoring.
    """
    cleaned = (value or "").strip()
    # Drop every non-ASCII character (mojibake / registered-sign artifacts).
    cleaned = "".join(ch for ch in cleaned if ord(ch) < 128)
    if cleaned.endswith("Ar"):
        return cleaned[:-2]
    if cleaned.endswith("r") and len(cleaned) > 1:
        return cleaned[:-1]
    return cleaned


def normalize_expected_cell(column: str, value: str | None) -> str:
    """Normalize an expected cell before comparison (whitelist cleanup)."""
    if column in ("MANUFACTURER_NAME", "BRAND_NAME"):
        return clean_dirty_brand(value or "")
    return (value or "").strip()


def is_leak(value: str) -> bool:
    """True when a derived-cell value is an official placeholder token."""
    return value.strip() in LEAK_TOKENS
