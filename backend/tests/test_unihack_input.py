"""Tests for the UniHack input parser (Step 6A).

Uses the dev/evaluation fixture copy of the official dataset
(``backend/tests/fixtures/Unihack_ Sample Dataset - Input.csv``). Production
code never reads this file; it is only here so the parser's real placeholder
semantics, duplicate detection, and row-error handling remain covered offline.
"""

import csv

import pytest

from app.unihack import (
    PLACEHOLDER_DIB_BRAND,
    PLACEHOLDER_E1_BRAND,
    PLACEHOLDER_PART_MANUF,
    PLACEHOLDER_UNILOG_BRAND,
    UniHackInputError,
    UniHackInputParser,
    UniHackInputResult,
    UniHackRowError,
)
from app.unihack.paths import input_fixture_path

parser = UniHackInputParser()


def test_loads_exactly_1000_rows():
    result = parser.parse_path(input_fixture_path())
    assert isinstance(result, UniHackInputResult)
    assert result.total_rows == 1000


def test_every_row_has_exactly_6_raw_fields():
    result = parser.parse_path(input_fixture_path())
    for row in result.rows:
        raw = [
            row.mfg_part_num,
            row.part_desc,
            row.e1_brand,
            row.unilog_brand,
            row.dib_brand,
            row.part_manuf,
        ]
        assert len(raw) == 6
        assert all(isinstance(value, str) for value in raw)


def test_raw_values_match_direct_csv_read(tmp_path):
    """Parser output must be byte-identical to a direct csv.reader read."""
    result = parser.parse_path(input_fixture_path())
    with input_fixture_path().open("r", encoding="utf-8-sig", newline="") as fh:
        direct = list(csv.reader(fh))[1:]
    assert len(direct) == result.total_rows
    for row, cells in zip(result.rows, direct):
        raw = [
            row.mfg_part_num,
            row.part_desc,
            row.e1_brand,
            row.unilog_brand,
            row.dib_brand,
            row.part_manuf,
        ]
        assert raw == cells


def test_placeholder_counts_on_real_file():
    result = parser.parse_path(input_fixture_path())
    assert result.placeholder_counts == {
        PLACEHOLDER_E1_BRAND: 799,
        PLACEHOLDER_UNILOG_BRAND: 1000,
        PLACEHOLDER_DIB_BRAND: 755,
        PLACEHOLDER_PART_MANUF: 41,
    }


def test_placeholder_rows_keep_raw_but_semantic_none():
    result = parser.parse_path(input_fixture_path())
    placeholder_rows = [
        row
        for row in result.rows
        if row.unilog_brand == PLACEHOLDER_UNILOG_BRAND
    ]
    assert placeholder_rows  # every row carries the Unilog placeholder
    sample = placeholder_rows[0]
    assert sample.unilog_brand == PLACEHOLDER_UNILOG_BRAND  # raw preserved
    assert sample.unilog_brand_value is None  # semantic missing
    assert "unilog_brand" in sample.missing_fields
    assert sample.mfg_part_num_value  # real values never degrade


def test_part_manuf_placeholder_semantic_none():
    result = parser.parse_path(input_fixture_path())
    dash_rows = [row for row in result.rows if row.part_manuf == "-"]
    assert len(dash_rows) == 41
    assert all(row.part_manuf_value is None for row in dash_rows)


def test_no_row_errors_on_real_file():
    result = parser.parse_path(input_fixture_path())
    assert result.row_errors == []


def test_avm6ev_duplicate_group_detected_and_kept():
    result = parser.parse_path(input_fixture_path())
    assert result.duplicate_groups == {"AVM6EV": [783, 784]}
    dupe_rows = [row for row in result.rows if row.mfg_part_num_duplicate]
    assert len(dupe_rows) == 2
    assert all(
        row.duplicate_group_id == "AVM6EV" and row.mfg_part_num == "AVM6EV"
        for row in dupe_rows
    )
    assert result.total_rows == 1000  # duplicates are reported, never removed


def test_to_identity_adapter():
    result = parser.parse_path(input_fixture_path())
    identity = result.rows[0].to_identity()
    assert identity.mpn == result.rows[0].mfg_part_num_value
    assert identity.raw_description == result.rows[0].part_desc_value
    assert identity.brand == (result.rows[0].e1_brand_value or "")


def test_placeholder_brand_leaves_identity_brand_empty():
    result = parser.parse_path(input_fixture_path())
    row = next(r for r in result.rows if r.e1_brand_value is None)
    assert row.to_identity().brand == ""


def test_malformed_column_count_is_row_error_not_fatal():
    text = (
        "Mfg_Part_Num,Part_Desc,E1_Brand,Unilog_Brand,DIB_Brand,Part_Manuf\n"
        "MPN1,desc,a,b,c,d\n"
        "MPN2,short desc\n"
        "MPN3,desc,a,b,c,d,e\n"
    )
    result = parser.parse_text(text)
    assert result.total_rows == 1
    assert len(result.row_errors) == 2
    assert [error.row_number for error in result.row_errors] == [3, 4]
    assert all("expected 6 columns" in e.message for e in result.row_errors)


def test_missing_required_fields_flagged_row_kept():
    text = (
        "Mfg_Part_Num,Part_Desc,E1_Brand,Unilog_Brand,DIB_Brand,Part_Manuf\n"
        ",no mpn here,a,b,c,d\n"
        "MPN2,,a,b,c,d\n"
        "MPN3,ok,a,b,c,d\n"
    )
    result = parser.parse_text(text)
    assert result.total_rows == 3  # incomplete rows are never dropped
    messages = [error.message for error in result.row_errors]
    assert len(messages) == 2
    assert "missing required field: Mfg_Part_Num" in messages
    assert "missing required field: Part_Desc" in messages


def test_wrong_header_is_fatal():
    with pytest.raises(UniHackInputError, match="header mismatch"):
        parser.parse_text("Wrong1,Wrong2\n")


def test_empty_file_is_fatal():
    with pytest.raises(UniHackInputError, match="empty"):
        parser.parse_text("")


def test_blank_lines_skipped():
    text = (
        "Mfg_Part_Num,Part_Desc,E1_Brand,Unilog_Brand,DIB_Brand,Part_Manuf\n"
        "MPN1,desc,a,b,c,d\n"
        "\n"
        "\n"
        "MPN2,desc2,a,b,c,d\n"
    )
    result = parser.parse_text(text)
    assert result.total_rows == 2
    assert [row.mfg_part_num_value for row in result.rows] == ["MPN1", "MPN2"]
    assert result.row_errors == []


def test_bom_in_text_is_stripped():
    text = (
        "\ufeffMfg_Part_Num,Part_Desc,E1_Brand,Unilog_Brand,DIB_Brand,"
        "Part_Manuf\nMPN1,desc,a,b,c,d\n"
    )
    result = parser.parse_text(text)
    assert result.total_rows == 1


def test_invalid_utf8_raises(tmp_path):
    bad = tmp_path / "bad.csv"
    bad.write_bytes(
        b"Mfg_Part_Num,Part_Desc,E1_Brand,Unilog_Brand,DIB_Brand,Part_Manuf\n"
        b"MPN1,\xff\xfe,a,b,c,d\n"
    )
    with pytest.raises(UniHackInputError, match="not valid UTF-8"):
        parser.parse_path(bad)


def test_missing_file_raises(tmp_path):
    with pytest.raises(UniHackInputError, match="not found"):
        parser.parse_path(tmp_path / "does_not_exist.csv")


def test_quoted_values_roundtrip():
    text = (
        "Mfg_Part_Num,Part_Desc,E1_Brand,Unilog_Brand,DIB_Brand,Part_Manuf\n"
        '"MPN,1","desc, with, commas",a,b,c,d\n'
    )
    result = parser.parse_text(text)
    assert result.rows[0].mfg_part_num == "MPN,1"
    assert result.rows[0].part_desc == "desc, with, commas"
