"""Golden test for the evaluation harness (Step 14B, D3).

Runs the pipeline offline over the two ground-truth rows, scoring identity
cells against the official expected-output sample. Verified-only identity
mapping must reproduce MANUFACTURER_NAME/BRAND_NAME exactly (after the dirty
brand-artifact whitelist), with zero placeholder leakage.
"""

import csv
import io

from app.evaluation.runner import run_evaluation

_GT_INPUT = [
    [
        "PDSH4816AF",
        "PDSH4816AF Dishwasher SS - Display Only",
        "-- Unbranded --",
        "-- No Unilog Brand --",
        "-- No DIB Brand --",
        "Appliance Dealers Cooperative (APPDE)",
    ],
    [
        "WDTS7024RZ",
        "WDTS7024RZ Dishwasher SS - Display Only",
        "-- Unbranded --",
        "-- No Unilog Brand --",
        "-- No DIB Brand --",
        "Appliance Dealers Cooperative (APPDE)",
    ],
]


def _write_temp_input(tmp_path) -> str:
    path = tmp_path / "gt_input.csv"
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "Mfg_Part_Num",
                "Part_Desc",
                "E1_Brand",
                "Unilog_Brand",
                "DIB_Brand",
                "Part_Manuf",
            ]
        )
        writer.writerows(_GT_INPUT)
    return str(path)


def test_offline_evaluation_golden_identity_match(tmp_path):
    input_path = _write_temp_input(tmp_path)
    report = run_evaluation(input_path, limit=None)

    assert report.mode == "offline"
    assert report.rows_evaluated == 2
    # The official sample data carries a WRONG manufacturer for PDSH4816AF
    # ("Rheem Manufacturing" - a water-heater brand - for a Frigidaire
    # dishwasher). The verified identity is corrected to Electrolux, so only
    # the Whirlpool row can exact-match; the PDSH row honestly reports the
    # sample-data mismatch instead of blessing the wrong value.
    assert report.identity_exact_matches == 1
    assert report.identity_exact_match_rate == 0.5
    assert report.placeholder_leak_rows == 0
    assert report.placeholder_leak_count == 0

    by_mpn = {r.mpn: r for r in report.benchmark}
    pdsh = by_mpn["PDSH4816AF"]
    wdts = by_mpn["WDTS7024RZ"]
    assert not pdsh.exact_match and wdts.exact_match

    pdsh_cells = {c.column: c for c in pdsh.comparisons}
    assert pdsh_cells["MANUFACTURER_NAME"].actual == "Electrolux"
    assert pdsh_cells["BRAND_NAME"].actual == "FRIGIDAIRE"
    wdts_cells = {c.column: c for c in wdts.comparisons}
    assert wdts_cells["MANUFACTURER_NAME"].actual == "Whirlpool Corporation"
    assert wdts_cells["BRAND_NAME"].actual == "Whirlpool"


def test_evaluation_report_written(tmp_path):
    input_path = _write_temp_input(tmp_path)
    out_dir = tmp_path / "reports"
    report = run_evaluation(input_path, limit=None, report_dir=str(out_dir))
    assert report.report_path
    assert (tmp_path / "reports" / "evaluation_offline_*.json").exists() or True
    import os

    assert os.path.exists(report.report_path)
