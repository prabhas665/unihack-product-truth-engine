"""Tests for the UniHack description rule engine (Step 14B, D1)."""

from app.core.domain.descriptions import Descriptions
from app.descriptions.rules import (
    MOBILE_MAX,
    MOBILE_MIN,
    UPPERCASE_OK_TOKENS,
    apply_description_rules,
    check_uppercase_runs,
    enforce_invoice,
    enforce_mobile,
    normalize_units,
)


def test_normalize_units_spells_out_inches():
    assert normalize_units("12IN") == "12 IN."
    assert normalize_units("50-1/4IN") == "50-1/4 IN."
    assert normalize_units('3"') == "3 IN."
    assert normalize_units("24INCHES") == "24 IN."


def test_normalize_units_spells_out_feet_and_metric():
    assert normalize_units("10FT") == "10 FT."
    assert normalize_units("350MM") == "350 MM"
    assert normalize_units("5CM") == "5 CM"
    assert normalize_units("2M") == "2 M"
    assert normalize_units("8LB") == "8 LB"
    assert normalize_units("4OZ") == "4 OZ"


def test_enforce_invoice_uppercases_and_strips_commas():
    value, reasons = enforce_invoice("Dishwasher, leg 5 sst 120v 15a 50-1/4in")
    assert value == "DISHWASHER LEG 5 SST 120V 15A 50-1/4 IN."
    assert len(value) <= 40
    assert any("uppercased" in r for r in reasons)
    assert any("units normalized" in r for r in reasons)


def test_enforce_invoice_supports_40_char_boundary():
    # Exactly 40 chars: must pass untouched (no compaction).
    exact = "X" * 40
    value, reasons = enforce_invoice(exact)
    assert value == exact
    assert not any("exceeded" in r for r in reasons)


def test_enforce_invoice_compacts_over_long():
    over = "DISHWASHER WITH LEG 5 SST 120V 15A 50-1/4 IN. EXTRA TOKEN"
    assert len(over) > 40
    value, reasons = enforce_invoice(over)
    assert len(value) <= 40
    assert any("compacted" in r for r in reasons)
    # connectors dropped first
    assert "WITH" not in value


def test_enforce_invoice_removes_stray_periods_keeps_unit_periods():
    value, _ = enforce_invoice("DRILL. 18V. WITH 2 BATTERY")
    assert value == "DRILL 18V WITH 2 BATTERY"
    value, _ = enforce_invoice("DRILL 18V FT. WITH 2 BATTERY")
    assert "FT." in value  # unit period preserved


def test_enforce_mobile_pads_spacing_and_normalizes_units():
    value, _ = enforce_mobile("Rheem 24INX24IN dishwasher professional series")
    # spacing added around X and units normalized
    assert "24 IN. x 24 IN." in value


def test_enforce_mobile_over_long_compacts():
    long = "X " * 100
    value, reasons = enforce_mobile(long)
    assert len(value) <= MOBILE_MAX
    assert any("compacted" in r for r in reasons)


def test_enforce_mobile_under_length_flagged_not_padded():
    value, reasons = enforce_mobile("DRILL 18V")
    assert value == "DRILL 18V"
    assert any("under" in r for r in reasons)


def test_check_uppercase_runs_accepts_unit_tokens():
    assert check_uppercase_runs("12 IN. 50 MM SST") == []
    assert "IN." in UPPERCASE_OK_TOKENS
    bad = check_uppercase_runs("FRIGIDAIRE PROFESSIONAL SERIES")
    # FRIGIDAIRE is >3 caps and not a unit token -> flagged
    assert any("FRIGIDAIRE" in r for r in bad)


def test_enforce_descriptions_rewrites_invoice_and_mobile():
    descriptions = Descriptions(
        invoice_description="dishwasher, leg 5 sst 120v",
        mobile_description="rheem 24inx24in dishwasher",
        short_description="A short one",
    )
    enforced, reasons = apply_description_rules(descriptions)
    assert enforced.invoice_description == "DISHWASHER LEG 5 SST 120V"
    assert "24 IN. x 24 IN." in enforced.mobile_description
    assert enforced.short_description == "A short one"
    assert any(r.startswith("INVOICE_DESC") for r in reasons)
    assert any(r.startswith("MOBILE_DESC") for r in reasons)
