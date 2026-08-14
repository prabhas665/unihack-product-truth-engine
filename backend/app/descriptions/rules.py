"""Deterministic UniHack description rule enforcement (Step 14B, D1).

The LLM produces commerce copy that may violate the official delivery format
constraints (length caps, casing, forbidden punctuation, unit spelling). This
module is the deterministic enforcement layer: it rewrites only what the rules
permit (casing, punctuation, unit normalization, length compaction) and reports
everything it changed as a review reason. It never invents facts.

Hard constraints enforced by rewriting:
- INVOICE_DESC: <= 40 chars, ALL CAPS, no commas, no periods except the unit
  tokens ``IN.``/``FT.``, normalized units, single spaces. Over-length is
  compacted by dropping connectors (WITH/&/AND) then trailing tokens.
- MOBILE_DESC: 60-80 chars, normalized units, spacing around the dimension
  multiplier ``X``, single spaces. Over-length is compacted at a word
  boundary; under-length is flagged (never padded - padding would invent copy).

Soft checks (reported, never rewritten) protect brand names and other
all-caps tokens from being mangled:
- No run of >3 consecutive uppercase letters unless the token is a recognized
  unit/operator token (e.g. IN., FT., MM, SST, DBA).
"""

from __future__ import annotations

import re

from app.core.domain.descriptions import Descriptions

INVOICE_MAX = 40
MOBILE_MIN = 60
MOBILE_MAX = 80

# Canonical unit spellings used in invoice/mobile descriptions.
UNIT_NORMALIZATION: dict[str, str] = {
    "INCHES": "IN.", "INCH": "IN.", "IN": "IN.",
    "FEET": "FT.", "FOOT": "FT.", "FT": "FT.",
    "METERS": "M", "METER": "M", "M": "M",
    "MILLIMETERS": "MM", "MILLIMETER": "MM", "MM": "MM",
    "CENTIMETERS": "CM", "CENTIMETER": "CM", "CM": "CM",
    "POUNDS": "LB", "POUND": "LB", "LBS": "LB", "LB": "LB",
    "OUNCES": "OZ", "OUNCE": "OZ", "OZ": "OZ",
}
# Longest keys first so "INCHES" wins over "IN".
_UNIT_RE = re.compile(
    r"(\d\.?\d*)\s*"
    r"(INCHES|INCH|IN|FEET|FOOT|FT|METERS|METER|M|"
    r"MILLIMETERS|MILLIMETER|MM|CENTIMETERS|CENTIMETER|CM|"
    r"POUNDS|POUND|LBS|LB|OUNCES|OUNCE|OZ)\b",
    re.IGNORECASE,
)
# The double-quote inch marker is handled separately (avoids raw-string escaping).
_INCH_QUOTE_RE = re.compile(r'(\d\.?\d*)\s*"')
# Dimension multiplier: a standalone X between numeric/unit tokens gets spaces
# and is lower-cased (e.g. "24INX24IN" -> "24IN x 24IN").
_SPACING_RE = re.compile(
    r"([0-9][0-9A-Za-z.%/-]*)\s*([Xx])\s*([0-9][0-9A-Za-z.%/-]*)"
)
_WS_RE = re.compile(r"\s+")

# Tokens exempt from the "no run of >3 uppercase letters" style check.
UPPERCASE_OK_TOKENS = {
    "IN.", "FT.", "MM", "CM", "M", "LB", "OZ", "V", "A", "DBA",
    "SST", "BLTLN", "NRP", "UPC", "SKU", "MPN", "LED", "USB", "PC",
}


def normalize_units(text: str) -> str:
    """Normalize numeric+unit phrases to canonical unit tokens (e.g. ``12IN``
    -> ``12 IN.``). Also inserts a space between a number and its unit."""
    text = _INCH_QUOTE_RE.sub(r"\1 IN", text)

    def _repl(match: re.Match[str]) -> str:
        num, unit = match.group(1), match.group(2).upper()
        return f"{num} {UNIT_NORMALIZATION[unit]}"

    return _UNIT_RE.sub(_repl, text)


def _add_spacing(text: str) -> str:
    """Insert spaces around the dimension multiplier, preserving its case
    (``24INX24IN`` -> ``24IN X 24IN``). Only fires between numeric/unit tokens."""
    return _SPACING_RE.sub(r"\1 \2 \3", text)


def enforce_invoice(text: str) -> tuple[str, list[str]]:
    """Return (enforced_value, reasons) for the INVOICE_DESC field."""
    issues: list[str] = []
    if not text:
        return "", issues

    value = text.strip()
    upper = value.upper()
    if upper != value:
        issues.append("invoice description was not all-uppercase; uppercased")
    value = upper

    normalized = normalize_units(value)
    if normalized != value:
        issues.append("invoice description units normalized")
    value = normalized
    value = _add_spacing(value)

    # Forbid commas; forbid periods except the unit tokens IN./FT.
    cleaned = value.replace(",", " ")
    cleaned = re.sub(r"(?<!IN|FT)\.(?=\s|$)", "", cleaned)
    cleaned = _WS_RE.sub(" ", cleaned).strip()
    if cleaned != value:
        issues.append("invoice description punctuation/spaces cleaned")
    value = cleaned

    if len(value) > INVOICE_MAX:
        issues.append(
            f"invoice description exceeded {INVOICE_MAX} chars "
            f"({len(value)}); compacted"
        )
        value = _compact_invoice(value)

    return value, issues


def _compact_invoice(value: str) -> str:
    """Drop connectors then trailing tokens until the value fits the cap."""
    for connector in (" WITH ", " & ", " AND "):
        value = value.replace(connector, " ")
    tokens = value.split(" ")
    deduplicated: list[str] = []
    for token in tokens:
        if not deduplicated or token != deduplicated[-1]:
            deduplicated.append(token)
    value = " ".join(deduplicated)
    while len(value) > INVOICE_MAX and " " in value:
        value = value.rsplit(" ", 1)[0]
    if len(value) > INVOICE_MAX:
        value = value[:INVOICE_MAX]
    return value


def enforce_mobile(text: str) -> tuple[str, list[str]]:
    """Return (enforced_value, reasons) for the MOBILE_DESC field."""
    issues: list[str] = []
    if not text:
        return "", issues

    value = text.strip()
    value = _add_spacing(value)
    value = normalize_units(value)
    # The mobile dimension multiplier is lower-case per the official format
    # (e.g. "23-7/8 in W x 22-5/8 in D").
    value = re.sub(r"(?i)\bX\b", "x", value)
    value = _WS_RE.sub(" ", value).strip()

    if len(value) > MOBILE_MAX:
        issues.append(
            f"mobile description exceeded {MOBILE_MAX} chars "
            f"({len(value)}); compacted at word boundary"
        )
        while len(value) > MOBILE_MAX and " " in value:
            value = value.rsplit(" ", 1)[0]
        if len(value) > MOBILE_MAX:
            value = value[:MOBILE_MAX]
    elif len(value) < MOBILE_MIN:
        issues.append(
            f"mobile description under {MOBILE_MIN} chars ({len(value)}); "
            f"left as-is (cannot pad without inventing copy)"
        )

    return value, issues


def check_uppercase_runs(text: str) -> list[str]:
    """Report runs of >3 consecutive uppercase letters that are not recognized
    unit/operator tokens. Detection only - never rewrites (protects brands)."""
    issues: list[str] = []
    for run in re.findall(r"[A-Z]{4,}", text):
        if run in UPPERCASE_OK_TOKENS:
            continue
        issues.append(
            f"uppercase run '{run}' is not a recognized unit/operator token"
        )
    return issues


def apply_description_rules(descriptions: Descriptions) -> tuple[Descriptions, list[str]]:
    """Apply the hard rules to invoice/mobile and soft-checks to the rest.

    Returns the (possibly rewritten, copies only) descriptions and a flat list
    of human-readable reasons for every change or flag.
    """
    reasons: list[str] = []
    invoice, inv_issues = enforce_invoice(descriptions.invoice_description)
    if inv_issues:
        reasons.append("INVOICE_DESC: " + "; ".join(inv_issues))
    mobile, mob_issues = enforce_mobile(descriptions.mobile_description)
    if mob_issues:
        reasons.append("MOBILE_DESC: " + "; ".join(mob_issues))

    for field, value in (
        ("INVOICE_DESC", invoice),
        ("MOBILE_DESC", mobile),
    ):
        runs = check_uppercase_runs(value)
        for run in runs:
            reasons.append(f"{field}: {run}")

    return (
        Descriptions(
            product_title=descriptions.product_title,
            short_description=descriptions.short_description,
            mobile_description=mobile,
            invoice_description=invoice,
            long_description=descriptions.long_description,
            retail_description=descriptions.retail_description,
            marketing_description=descriptions.marketing_description,
            item_features=list(descriptions.item_features),
            with_=descriptions.with_,
            application=descriptions.application,
            includes=descriptions.includes,
            product_name=descriptions.product_name,
        ),
        reasons,
    )
