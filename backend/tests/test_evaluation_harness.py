"""Tests for the offline delivery evaluation harness (P0).

The evaluator only reads CSV files - no Groq, no OpenRouter, no external
URLs. Fixtures use synthetic 252-column CSVs (organizer-shaped headers),
NOT real UniHack data.
"""

import csv
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.eval_delivery import (  # noqa: E402
    DESCRIPTION_COLUMNS,
    EXPECTED_COLUMN_COUNT,
    FAIL,
    NOT_SCOREABLE,
    PARTIAL,
    PASS,
    load_rows,
    norm_text,
    pair_rows,
    registrable_domain,
    render_text,
    run_evaluation,
    validate_schema,
)

CORE_HEADERS = [
    "MFR URL", "Ref URL 1", "Ref URL 2", "Ref URL 3", "Ref URL 4", "Ref URL 5",
    "PART_NUMBER", "Dept", "Class", "Fine", "SKU - MY_PART_NUMBER",
    "Mfg_Part_Num", "Part_Desc", "E1_Brand", "Unilog_Brand", "DIB_Brand",
    "Part_Manuf", "MANUFACTURER_NAME", "BRAND_NAME", "TRADE_NAME",
    "MANUFACTURER_PART_NUMBER", "ALTERNATE_PART_NUMBER", "Classpath",
    "MOBILE_DESC", "INVOICE_DESC", "SHORT_DESC", "LONG_DESC1",
    "RETAIL_DESC", "MARKETING_DESCRIPTION", "ITEM_FEATURES_1",
]


def headers252() -> list[str]:
    headers = list(CORE_HEADERS)
    index = 1
    while len(headers) < EXPECTED_COLUMN_COUNT:
        headers.append(f"COL_{index}")
        index += 1
    return headers


def write_csv(path: Path, headers: list[str], rows: list[dict[str, str]]) -> Path:
    with open(path, "w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=headers)
        writer.writeheader()
        for row in rows:
            writer.writerow({h: row.get(h, "") for h in headers})
    return path


def blank_row(headers: list[str]) -> dict[str, str]:
    return {h: "" for h in headers}


def expected_row(
    mpn: str,
    *,
    manufacturer: str = "Whirlpool Corporation",
    brand: str = "Whirlpool",
    part_desc: str = "WDTS7024RZ Dishwasher SS - Display Only",
    mfr_url: str = "https://www.whirlpool.com/products/wdts7024rz",
    part_number: str = "25286031",
) -> dict[str, str]:
    row = blank_row(headers252())
    row.update({
        "MFR URL": mfr_url,
        "PART_NUMBER": part_number,
        "Mfg_Part_Num": mpn,
        "Part_Desc": part_desc,
        "MANUFACTURER_NAME": manufacturer,
        "BRAND_NAME": brand,
        "MANUFACTURER_PART_NUMBER": mpn,
        "MOBILE_DESC": "Whirlpool, Dishwasher, Eco Series, WDTS7024RZ",
        "SHORT_DESC": "Whirlpool Eco Series WDTS7024RZ Dishwasher",
        "LONG_DESC1": "Whirlpool Dishwasher Eco Series 120 V 10 A Built-in Mounting",
        "RETAIL_DESC": "Eco Series Dishwasher, Built-in Mounting",
        "MARKETING_DESCRIPTION": "Quietest and largest capacity dishwasher",
        "INVOICE_DESC": "DISHWASHER BLTLN SST",
    })
    return row


def generated_row(
    mpn: str,
    *,
    manufacturer: str = "Whirlpool Corporation",
    brand: str = "Whirlpool",
    part_desc: str = "WDTS7024RZ Dishwasher SS - Display Only",
    mfr_url: str = "https://whirlpool.ca/products/wdts7024rz",
    descriptions: bool = True,
) -> dict[str, str]:
    row = blank_row(headers252())
    row.update({
        "MFR URL": mfr_url,
        "Mfg_Part_Num": mpn,
        "Part_Desc": part_desc,
        "MANUFACTURER_NAME": manufacturer,
        "BRAND_NAME": brand,
        "MANUFACTURER_PART_NUMBER": mpn,
    })
    if descriptions:
        row.update({
            "MOBILE_DESC": "Whirlpool Dishwasher Eco Series WDTS7024RZ",
            "INVOICE_DESC": "DISHWASHER SST",
            "SHORT_DESC": "Whirlpool WDTS7024RZ Eco Series Dishwasher",
            "LONG_DESC1": "Whirlpool Eco Series Dishwasher 120V built-in mounting",
            "RETAIL_DESC": "Eco Series Dishwasher Stainless Steel",
            "MARKETING_DESCRIPTION": "Our quietest dishwasher",
        })
    return row


@pytest.fixture
def expected_csv(tmp_path: Path) -> Path:
    rows = [expected_row("WDTS7024RZ")]
    return write_csv(tmp_path / "expected.csv", headers252(), rows)


@pytest.fixture
def generated_csv(tmp_path: Path) -> Path:
    rows = [generated_row("WDTS7024RZ")]
    return write_csv(tmp_path / "generated.csv", headers252(), rows)


class TestNormalization:
    def test_mojibake_normalized_away(self):
        assert norm_text("Whirlpool\uFFFD") == "whirlpool"
        assert norm_text("FRIGIDAIRE\uFFFD") == "frigidaire"

    def test_norm_strips_punctuation_and_collapses(self):
        assert norm_text("  Dishwasher, 120-V   (SST)  ") == "dishwasher 120 v sst"

    def test_registrable_domain(self):
        assert registrable_domain("https://www.whirlpool.com/x") == "whirlpool.com"
        assert registrable_domain("https://whirlpool.ca/p") == "whirlpool.ca"
        assert registrable_domain("not a url") is None


class TestCsvLoading:
    def test_load_rows_with_leading_bom_and_spaces(self, tmp_path):
        path = tmp_path / "e.csv"
        with open(path, "w", encoding="utf-8-sig", newline="") as handle:
            handle.write('" Mfg_Part_Num ",MANUFACTURER_NAME\n')
            handle.write("  WDTS7024RZ  ,Whirlpool Corporation\n")
        rows = load_rows(str(path))
        assert rows[0]["Mfg_Part_Num"] == "  WDTS7024RZ  "

    def test_malformed_duplicate_headers_raise(self, tmp_path):
        path = tmp_path / "bad.csv"
        path.write_text("Mfg_Part_Num,Mfg_Part_Num\nA,B\n", encoding="utf-8")
        with pytest.raises(ValueError, match="duplicate"):
            load_rows(str(path))

    def test_missing_mpn_column_raises(self, tmp_path):
        path = tmp_path / "bad.csv"
        path.write_text("MANUFACTURER_NAME\nWhirlpool\n", encoding="utf-8")
        with pytest.raises(ValueError, match="Mfg_Part_Num"):
            load_rows(str(path))

    def test_wrong_column_count_raises(self, tmp_path):
        rows = [{"Mfg_Part_Num": "WDTS7024RZ", "MANUFACTURER_NAME": "Whirlpool"}]
        path = write_csv(tmp_path / "short.csv",
                         ["Mfg_Part_Num", "MANUFACTURER_NAME"], rows)
        with pytest.raises(ValueError, match="252"):
            validate_schema(str(path), load_rows(str(path)))

    def test_252_columns_accepted(self, expected_csv):
        rows = load_rows(str(expected_csv))
        validate_schema(str(expected_csv), rows)
        assert len(rows[0]) == EXPECTED_COLUMN_COUNT


class TestPairing:
    def test_pairing_by_normalized_mpn(self):
        exp = [{"Mfg_Part_Num": " WDTS7024RZ "}]
        gen = [{"Mfg_Part_Num": "wdts7024rz"}]
        pairs, unexp, ungen = pair_rows(exp, gen)
        assert set(pairs) == {"WDTS7024RZ"}
        assert unexp == []
        assert ungen == []

    def test_mpn_mismatch_reported_unpaired(self):
        exp = [{"Mfg_Part_Num": "PDSH4816AF"}]
        gen = [{"Mfg_Part_Num": "WDTS7024RZ"}]
        pairs, unexp, ungen = pair_rows(exp, gen)
        assert pairs == {}
        assert unexp == ["PDSH4816AF"]
        assert ungen == ["WDTS7024RZ"]


class TestScoring:
    def test_mpn_manufacturer_brand_exact_normalized(self, expected_csv, generated_csv):
        report = run_evaluation(str(expected_csv), str(generated_csv))
        metrics = report.per_product[0]["metrics"]
        assert metrics["mpn_identity"]["status"] == PASS
        assert metrics["manufacturer_name"]["status"] == PASS
        assert metrics["brand_name"]["status"] == PASS

    def test_brand_mojibake_still_passes(self, tmp_path):
        exp = [expected_row("WDTS7024RZ", brand="Whirlpool\uFFFD")]
        gen = [generated_row("WDTS7024RZ")]
        e = write_csv(tmp_path / "e.csv", headers252(), exp)
        g = write_csv(tmp_path / "g.csv", headers252(), gen)
        report = run_evaluation(str(e), str(g))
        assert report.per_product[0]["metrics"]["brand_name"]["status"] == PASS

    def test_manufacturer_mismatch_fails(self, tmp_path):
        exp = [expected_row("WDTS7024RZ")]
        gen = [generated_row("WDTS7024RZ", manufacturer="Rheem Manufacturing")]
        e = write_csv(tmp_path / "e.csv", headers252(), exp)
        g = write_csv(tmp_path / "g.csv", headers252(), gen)
        report = run_evaluation(str(e), str(g))
        assert report.per_product[0]["metrics"]["manufacturer_name"]["status"] == FAIL

    @pytest.mark.parametrize(
        ("ours", "status"),
        [
            ("WDTS7024RZ Dishwasher SS - Display Only", PASS),
            ("WDTS7024RZ Dishwasher SS - Display Only extra words about", PARTIAL),
            ("Completely unrelated product text", FAIL),
            ("", FAIL),
        ],
    )
    def test_part_desc_token_comparison(self, tmp_path, ours, status):
        exp = [expected_row("WDTS7024RZ")]
        gen = [generated_row("WDTS7024RZ", part_desc=ours)]
        e = write_csv(tmp_path / "e.csv", headers252(), exp)
        g = write_csv(tmp_path / "g.csv", headers252(), gen)
        report = run_evaluation(str(e), str(g))
        assert report.per_product[0]["metrics"]["part_desc"]["status"] == status

    @pytest.mark.parametrize(
        ("descriptions", "status"),
        [(True, PASS), (False, FAIL)],
    )
    def test_description_completeness(self, tmp_path, descriptions, status):
        exp = [expected_row("WDTS7024RZ")]
        gen = [generated_row("WDTS7024RZ", descriptions=descriptions)]
        e = write_csv(tmp_path / "e.csv", headers252(), exp)
        g = write_csv(tmp_path / "g.csv", headers252(), gen)
        report = run_evaluation(str(e), str(g))
        metric = report.per_product[0]["metrics"]["description_completeness"]
        assert metric["status"] == status

    def test_partial_description_completeness(self, tmp_path):
        gen_row = generated_row("WDTS7024RZ")
        for col in DESCRIPTION_COLUMNS[:2]:
            gen_row[col] = ""
        e = write_csv(tmp_path / "e.csv", headers252(), [expected_row("WDTS7024RZ")])
        g = write_csv(tmp_path / "g.csv", headers252(), [gen_row])
        report = run_evaluation(str(e), str(g))
        assert report.per_product[0]["metrics"]["description_completeness"]["status"] == PARTIAL

    def test_part_number_marked_not_scoreable(self, expected_csv, generated_csv):
        report = run_evaluation(str(expected_csv), str(generated_csv))
        assert report.per_product[0]["metrics"]["part_number"]["status"] == NOT_SCOREABLE

    def test_attributes_marked_not_scoreable(self, expected_csv, generated_csv):
        report = run_evaluation(str(expected_csv), str(generated_csv))
        metric = report.per_product[0]["metrics"]["attributes_precision_recall"]
        assert metric["status"] == NOT_SCOREABLE

    def test_lov_uom_marked_not_scoreable(self, expected_csv, generated_csv):
        report = run_evaluation(str(expected_csv), str(generated_csv))
        metric = report.per_product[0]["metrics"]["classification_lov_uom"]
        assert metric["status"] == NOT_SCOREABLE

    def test_mfr_url_registrable_domain_pass(self, tmp_path):
        exp = [expected_row("WDTS7024RZ")]
        gen = [generated_row("WDTS7024RZ",
                            mfr_url="https://www.whirlpool.com/products/wdts7024rz")]
        e = write_csv(tmp_path / "e.csv", headers252(), exp)
        g = write_csv(tmp_path / "g.csv", headers252(), gen)
        report = run_evaluation(str(e), str(g))
        assert report.per_product[0]["metrics"]["mfr_url_relevance"]["status"] == PASS

    def test_mfr_url_different_domain_partial(self, tmp_path):
        exp = [expected_row("WDTS7024RZ")]
        gen = [generated_row("WDTS7024RZ",
                            mfr_url="https://www.learnwhirlpool.com/smartsearch?q=WDTS7024RZ")]
        e = write_csv(tmp_path / "e.csv", headers252(), exp)
        g = write_csv(tmp_path / "g.csv", headers252(), gen)
        report = run_evaluation(str(e), str(g))
        assert report.per_product[0]["metrics"]["mfr_url_relevance"]["status"] == PARTIAL

    def test_mfr_url_blank_fails(self, tmp_path):
        exp = [expected_row("WDTS7024RZ")]
        gen = [generated_row("WDTS7024RZ", mfr_url="")]
        e = write_csv(tmp_path / "e.csv", headers252(), exp)
        g = write_csv(tmp_path / "g.csv", headers252(), gen)
        report = run_evaluation(str(e), str(g))
        assert report.per_product[0]["metrics"]["mfr_url_relevance"]["status"] == FAIL

    def test_sibling_mpn_detected_in_description(self, tmp_path):
        gen_row = generated_row("WDTS7024RZ")
        gen_row["SHORT_DESC"] = "Also compatible with the XLC10ZW Makita vacuum"
        e = write_csv(tmp_path / "e.csv", headers252(), [expected_row("WDTS7024RZ")])
        g = write_csv(tmp_path / "g.csv", headers252(), [gen_row])
        report = run_evaluation(str(e), str(g), extra_mpns=["XLC10ZW"])
        metric = report.per_product[0]["metrics"]["mpn_isolation"]
        assert metric["status"] == FAIL
        assert "XLC10ZW" in metric["detail"]

    def test_clean_row_mpn_isolation_passes(self, expected_csv, generated_csv):
        report = run_evaluation(str(expected_csv), str(generated_csv),
                                extra_mpns=["XLC10ZW", "PDSH4816AF"])
        assert report.per_product[0]["metrics"]["mpn_isolation"]["status"] == PASS


class TestReports:
    def test_row_count_mismatch_reported(self, tmp_path):
        exp = [expected_row("WDTS7024RZ"), expected_row("PDSH4816AF", brand="FRIGIDAIRE")]
        gen = [generated_row("WDTS7024RZ")]
        e = write_csv(tmp_path / "e.csv", headers252(), exp)
        g = write_csv(tmp_path / "g.csv", headers252(), gen)
        report = run_evaluation(str(e), str(g))
        assert report.expected_rows == 2
        assert report.generated_rows == 1
        assert report.paired_rows == 1
        assert report.unpaired_expected == ["PDSH4816AF"]
        assert report.unpaired_generated == []

    def test_aggregate_and_failed_field_reports(self, tmp_path):
        exp = [expected_row("WDTS7024RZ")]
        gen_row = generated_row("WDTS7024RZ", manufacturer="Wrong Corp",
                                mfr_url="", descriptions=False)
        e = write_csv(tmp_path / "e.csv", headers252(), exp)
        g = write_csv(tmp_path / "g.csv", headers252(), [gen_row])
        report = run_evaluation(str(e), str(g))
        assert report.aggregate["manufacturer_name"]["FAIL"] == 1
        assert report.aggregate["description_completeness"]["FAIL"] == 1
        failed_metrics = {f["metric"] for f in report.failed_fields}
        assert {"manufacturer_name", "description_completeness", "mfr_url_relevance"} <= failed_metrics
        assert report.overall_score is not None
        assert report.scoreable_metrics_count > 0

    def test_not_scoreable_report_lists_reasons(self, expected_csv, generated_csv):
        report = run_evaluation(str(expected_csv), str(generated_csv))
        ns = {item["metric"] for item in report.not_scoreable}
        assert {"part_number", "attributes_precision_recall", "classification_lov_uom"} <= ns

    def test_render_text_contains_sections(self, expected_csv, generated_csv):
        report = run_evaluation(str(expected_csv), str(generated_csv))
        text = render_text(report)
        assert "DELIVERY EVALUATION REPORT" in text
        assert "-- per product --" in text
        assert "-- aggregate --" in text
        assert "-- failed fields --" in text
        assert "-- not scoreable --" in text
        assert "paired rows" in text
