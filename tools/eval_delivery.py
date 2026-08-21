"""Offline delivery evaluator for the UniHack P0 evaluation harness.

Compares a generated delivery CSV against the organizer's Expected Output
(Delivery Format) CSV. Strictly OFFLINE: reads two CSV files, never calls
Groq/OpenRouter, never fetches external URLs.

Rows are paired by normalized Mfg_Part_Num. Every metric is reported as
PASS / FAIL / PARTIAL / NOT_SCOREABLE. Fields whose organizer ground truth
does not exist (PART_NUMBER distributor SKU, attribute columns blank in the
expected file, classification/LOV/UOM taxonomy) are marked NOT_SCOREABLE -
they are never scored as product failures.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from dataclasses import asdict, dataclass, field
from typing import Any
from urllib.parse import urlsplit

EXPECTED_COLUMN_COUNT = 252
MPN_COLUMN = "Mfg_Part_Num"

DESCRIPTION_COLUMNS = [
    "MOBILE_DESC",
    "INVOICE_DESC",
    "SHORT_DESC",
    "LONG_DESC1",
    "RETAIL_DESC",
    "MARKETING_DESCRIPTION",
]

ISOLATION_SCAN_COLUMNS = [
    "MFR URL",
    "Part_Desc",
    "MANUFACTURER_PART_NUMBER",
    "MOBILE_DESC",
    "INVOICE_DESC",
    "SHORT_DESC",
    "LONG_DESC1",
    "RETAIL_DESC",
    "MARKETING_DESCRIPTION",
    "ITEM_FEATURES_1",
    "ITEM_FEATURES_2",
]

PASS = "PASS"
FAIL = "FAIL"
PARTIAL = "PARTIAL"
NOT_SCOREABLE = "NOT_SCOREABLE"

TOKEN_PASS_RATIO = 0.66
TOKEN_PARTIAL_RATIO = 0.33

STATUS_WEIGHT = {PASS: 1.0, PARTIAL: 0.5, FAIL: 0.0}

_NOT_SCOREABLE_REASONS = {
    "part_number": (
        "expected PART_NUMBER is an organizer distributor SKU with no source"
    ),
    "attributes_precision_recall": (
        "organizer expected output leaves attribute columns blank"
    ),
    "classification_lov_uom": (
        "no organizer LOV/UOM/taxonomy resource exists"
    ),
}


def norm_text(value: Any) -> str:
    """Lower-case alnum-only normalized text (kills mojibake/registered marks)."""
    text = str(value or "").lower().strip()
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def token_set(value: Any) -> set[str]:
    """Alphanumeric token set used for Part_Desc comparison."""
    return set(norm_text(value).split())


def mpn_key(value: Any) -> str:
    """Normalized MPN key used for row pairing."""
    return str(value or "").strip().upper()


def registrable_domain(url: str) -> str | None:
    """Last two labels of the host (www stripped); None for non-http(s)."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return None
    if parts.scheme.lower() not in ("http", "https") or not parts.hostname:
        return None
    host = parts.hostname.lower()
    if host.startswith("www."):
        host = host[4:]
    labels = host.split(".")
    if len(labels) >= 2:
        return ".".join(labels[-2:])
    return host


def _jaccard(left: set[str], right: set[str]) -> float:
    union = left | right
    if not union:
        return 1.0
    return len(left & right) / len(union)


def _status_from_ratio(ratio: float) -> str:
    if ratio >= TOKEN_PASS_RATIO:
        return PASS
    if ratio >= TOKEN_PARTIAL_RATIO:
        return PARTIAL
    return FAIL


@dataclass(frozen=True)
class MetricResult:
    metric: str
    status: str
    value: Any = None
    detail: str = ""


@dataclass(frozen=True)
class RowResult:
    mpn: str
    metrics: list[MetricResult]


def load_rows(path: str) -> list[dict[str, str]]:
    """Load a CSV as header-keyed rows with structural validation."""
    try:
        with open(path, encoding="utf-8-sig", newline="") as handle:
            reader = csv.reader(handle)
            raw = list(reader)
    except OSError as exc:
        raise ValueError(f"{path}: cannot read file: {exc}") from exc
    except csv.Error as exc:
        raise ValueError(f"{path}: malformed CSV: {exc}") from exc
    if not raw:
        raise ValueError(f"{path}: empty file")
    headers = [h.strip() for h in raw[0]]
    if len(headers) != len(set(headers)):
        raise ValueError(f"{path}: duplicate column headers")
    if MPN_COLUMN not in headers:
        raise ValueError(f"{path}: missing required column {MPN_COLUMN!r}")
    rows: list[dict[str, str]] = []
    for line in raw[1:]:
        if not any(cell.strip() for cell in line):
            continue
        row = {headers[i]: (line[i] if i < len(line) else "") for i in range(len(headers))}
        rows.append(row)
    return rows


def validate_schema(path: str, rows: list[dict[str, str]]) -> None:
    """Enforce the exact 252-column delivery schema."""
    if not rows:
        raise ValueError(f"{path}: no data rows")
    if len(rows[0]) != EXPECTED_COLUMN_COUNT:
        raise ValueError(
            f"{path}: expected {EXPECTED_COLUMN_COUNT} columns, got {len(rows[0])}"
        )


def pair_rows(
    expected: list[dict[str, str]], generated: list[dict[str, str]]
) -> tuple[dict[str, tuple[dict[str, str], dict[str, str]]], list[str], list[str]]:
    """Pair by normalized Mfg_Part_Num; report unpaired MPNs."""
    pairs: dict[str, tuple[dict[str, str], dict[str, str]]] = {}
    exp_by_key = {mpn_key(r.get(MPN_COLUMN)): r for r in expected}
    gen_by_key = {mpn_key(r.get(MPN_COLUMN)): r for r in generated}
    for key, exp_row in exp_by_key.items():
        if key in gen_by_key:
            pairs[key] = (exp_row, gen_by_key[key])
    unpaired_expected = sorted(set(exp_by_key) - set(pairs))
    unpaired_generated = sorted(set(gen_by_key) - set(pairs))
    return pairs, unpaired_expected, unpaired_generated


def _exact_norm(metric: str, ours: Any, expected: Any) -> MetricResult:
    ours_n = norm_text(ours)
    exp_n = norm_text(expected)
    if not exp_n:
        return MetricResult(metric, NOT_SCOREABLE, value=ours_n,
                            detail="expected value is blank in the ground truth")
    if not ours_n:
        return MetricResult(metric, FAIL, value=ours_n,
                            detail=f"generated value is blank (expected {exp_n!r})")
    if ours_n == exp_n:
        return MetricResult(metric, PASS, value=ours_n)
    return MetricResult(metric, FAIL, value=ours_n, detail=f"expected {exp_n!r}")


def _part_desc(ours: Any, expected: Any) -> MetricResult:
    exp_tokens = token_set(expected)
    if not exp_tokens:
        return MetricResult("part_desc", NOT_SCOREABLE,
                            value=None, detail="expected Part_Desc is blank")
    ours_tokens = token_set(ours)
    if not ours_tokens:
        return MetricResult("part_desc", FAIL, value=0.0,
                            detail="generated Part_Desc is blank")
    ratio = _jaccard(ours_tokens, exp_tokens)
    return MetricResult("part_desc", _status_from_ratio(ratio), value=round(ratio, 3))


def _description_completeness(row: dict[str, str]) -> MetricResult:
    present = [c for c in DESCRIPTION_COLUMNS if norm_text(row.get(c))]
    ratio = len(present) / len(DESCRIPTION_COLUMNS)
    status = PASS if ratio >= 1.0 else (PARTIAL if ratio > 0.0 else FAIL)
    return MetricResult(
        "description_completeness", status, value=ratio,
        detail=f"{len(present)}/{len(DESCRIPTION_COLUMNS)} variants populated",
    )


def _mfr_url(ours: Any, expected: Any) -> MetricResult:
    ours_s = str(ours or "").strip()
    exp_s = str(expected or "").strip()
    if not exp_s:
        return MetricResult("mfr_url_relevance", NOT_SCOREABLE,
                            value=ours_s, detail="expected MFR URL is blank")
    if not ours_s:
        return MetricResult("mfr_url_relevance", FAIL, value=ours_s,
                            detail="generated MFR URL is blank")
    ours_domain = registrable_domain(ours_s)
    exp_domain = registrable_domain(exp_s)
    if ours_domain and ours_domain == exp_domain:
        return MetricResult("mfr_url_relevance", PASS,
                            value=ours_s, detail=f"registrable domain {ours_domain!r}")
    return MetricResult(
        "mfr_url_relevance", PARTIAL, value=ours_s,
        detail=(
            f"generated {ours_domain!r} vs expected {exp_domain!r}; "
            "trust-policy differences make strict equality not enforceable"
        ),
    )


def _mpn_isolation(row: dict[str, str], foreign_mpns: list[str]) -> MetricResult:
    found: list[str] = []
    text = " ".join(str(row.get(c) or "") for c in ISOLATION_SCAN_COLUMNS).lower()
    for foreign in sorted(foreign_mpns):
        token = foreign.lower()
        if len(token) < 3:
            continue
        if token in text:
            found.append(foreign)
    if found:
        return MetricResult("mpn_isolation", FAIL, value=found,
                            detail=f"row cites foreign MPN(s): {', '.join(found)}")
    return MetricResult("mpn_isolation", PASS, value=[])


def evaluate_row(
    exp_row: dict[str, str],
    gen_row: dict[str, str],
    foreign_mpns: list[str],
) -> RowResult:
    """Score one paired product against its expected row."""
    mpn = mpn_key(exp_row.get(MPN_COLUMN))
    metrics: list[MetricResult] = [
        _exact_norm("mpn_identity", gen_row.get(MPN_COLUMN), exp_row.get(MPN_COLUMN)),
        _exact_norm("manufacturer_name",
                    gen_row.get("MANUFACTURER_NAME"), exp_row.get("MANUFACTURER_NAME")),
        _exact_norm("brand_name", gen_row.get("BRAND_NAME"), exp_row.get("BRAND_NAME")),
        MetricResult("part_number", NOT_SCOREABLE,
                     value=gen_row.get("PART_NUMBER"),
                     detail=_NOT_SCOREABLE_REASONS["part_number"]),
        _part_desc(gen_row.get("Part_Desc"), exp_row.get("Part_Desc")),
        _description_completeness(gen_row),
        _mfr_url(gen_row.get("MFR URL"), exp_row.get("MFR URL")),
        MetricResult("attributes_precision_recall", NOT_SCOREABLE,
                     value=None,
                     detail=_NOT_SCOREABLE_REASONS["attributes_precision_recall"]),
        MetricResult("classification_lov_uom", NOT_SCOREABLE,
                     value=None,
                     detail=_NOT_SCOREABLE_REASONS["classification_lov_uom"]),
        _mpn_isolation(gen_row, foreign_mpns),
    ]
    return RowResult(mpn=mpn, metrics=metrics)


def _product_score(metrics: list[MetricResult]) -> float | None:
    weights = [STATUS_WEIGHT[m.status] for m in metrics if m.status in STATUS_WEIGHT]
    if not weights:
        return None
    return round(sum(weights) / len(weights), 3)


@dataclass
class EvaluationReport:
    expected_path: str
    generated_path: str
    generated_rows: int
    expected_rows: int
    paired_rows: int
    unpaired_expected: list[str]
    unpaired_generated: list[str]
    per_product: list[dict[str, Any]] = field(default_factory=list)
    aggregate: dict[str, dict[str, Any]] = field(default_factory=dict)
    overall_score: float | None = None
    scoreable_metrics_count: int = 0
    failed_fields: list[dict[str, Any]] = field(default_factory=list)
    not_scoreable: list[dict[str, Any]] = field(default_factory=list)


def run_evaluation(
    expected_path: str,
    generated_path: str,
    extra_mpns: list[str] | None = None,
) -> EvaluationReport:
    """Score a generated delivery CSV against the organizer expected CSV."""
    expected = load_rows(expected_path)
    generated = load_rows(generated_path)
    validate_schema(expected_path, expected)
    validate_schema(generated_path, generated)

    pairs, unpaired_expected, unpaired_generated = pair_rows(expected, generated)

    known_mpns = (
        {mpn_key(r.get(MPN_COLUMN)) for r in expected}
        | {mpn_key(r.get(MPN_COLUMN)) for r in generated}
        | {mpn_key(m) for m in (extra_mpns or [])}
    )

    report = EvaluationReport(
        expected_path=expected_path,
        generated_path=generated_path,
        generated_rows=len(generated),
        expected_rows=len(expected),
        paired_rows=len(pairs),
        unpaired_expected=unpaired_expected,
        unpaired_generated=unpaired_generated,
    )

    metric_names: list[str] = []
    for key, (exp_row, gen_row) in sorted(pairs.items()):
        foreign = sorted(known_mpns - {key})
        row_result = evaluate_row(exp_row, gen_row, foreign)
        row_dict = {
            "mpn": row_result.mpn,
            "metrics": {m.metric: asdict(m) for m in row_result.metrics},
            "product_score": _product_score(row_result.metrics),
        }
        report.per_product.append(row_dict)
        if not metric_names:
            metric_names = [m.metric for m in row_result.metrics]

    for name in metric_names:
        counts = {PASS: 0, FAIL: 0, PARTIAL: 0, NOT_SCOREABLE: 0}
        for row in report.per_product:
            status = row["metrics"][name]["status"]
            counts[status] = counts.get(status, 0) + 1
        weighted = [
            STATUS_WEIGHT[row["metrics"][name]["status"]]
            for row in report.per_product
            if row["metrics"][name]["status"] in STATUS_WEIGHT
        ]
        score = round(sum(weighted) / len(weighted), 3) if weighted else None
        counts["score"] = score
        report.aggregate[name] = counts

    scoreable = 0
    weighted_all: list[float] = []
    for row in report.per_product:
        for metric in row["metrics"].values():
            if metric["status"] in STATUS_WEIGHT:
                scoreable += 1
                weighted_all.append(STATUS_WEIGHT[metric["status"]])
    report.scoreable_metrics_count = scoreable
    if weighted_all:
        report.overall_score = round(sum(weighted_all) / len(weighted_all), 3)

    for row in report.per_product:
        for name, metric in row["metrics"].items():
            if metric["status"] == FAIL:
                report.failed_fields.append(
                    {"mpn": row["mpn"], "metric": name, "value": metric["value"],
                     "detail": metric["detail"]}
                )
            if metric["status"] == NOT_SCOREABLE:
                report.not_scoreable.append(
                    {"mpn": row["mpn"], "metric": name, "detail": metric["detail"]}
                )

    return report


def render_text(report: EvaluationReport) -> str:
    """Human-readable rendering of an EvaluationReport."""
    lines: list[str] = []
    lines.append("=" * 72)
    lines.append("DELIVERY EVALUATION REPORT (offline)")
    lines.append("=" * 72)
    lines.append(f"expected file : {report.expected_path}")
    lines.append(f"generated file: {report.generated_path}")
    lines.append(f"generated rows : {report.generated_rows}")
    lines.append(f"expected rows  : {report.expected_rows}")
    lines.append(f"paired rows    : {report.paired_rows}")
    lines.append(f"unpaired expected : {report.unpaired_expected}")
    lines.append(f"unpaired generated: {report.unpaired_generated}")
    overall = f"{report.overall_score}" if report.overall_score is not None else "n/a"
    lines.append(f"overall score  : {overall} (over {report.scoreable_metrics_count} scoreable metrics)")

    lines.append("")
    lines.append("-- per product --")
    for row in report.per_product:
        lines.append(f"* {row['mpn']}  product_score={row['product_score']}")
        for name, metric in row["metrics"].items():
            value = "" if metric["value"] is None else f"  value={metric['value']}"
            detail = f"  ({metric['detail']})" if metric["detail"] else ""
            lines.append(f"    {name:<28} {metric['status']:<13}{value}{detail}")

    lines.append("")
    lines.append("-- aggregate --")
    for name, counts in report.aggregate.items():
        score = counts.get("score")
        score_txt = "n/a" if score is None else f"{score}"
        lines.append(
            f"{name:<28} PASS={counts[PASS]} PARTIAL={counts[PARTIAL]} "
            f"FAIL={counts[FAIL]} NS={counts[NOT_SCOREABLE]} score={score_txt}"
        )

    lines.append("")
    lines.append("-- failed fields --")
    if not report.failed_fields:
        lines.append("  (none)")
    for item in report.failed_fields:
        lines.append(f"  {item['mpn']} {item['metric']}: value={item['value']!r} {item['detail']}")

    lines.append("")
    lines.append("-- not scoreable --")
    if not report.not_scoreable:
        lines.append("  (none)")
    seen: set[tuple[str, str]] = set()
    for item in report.not_scoreable:
        key = (item["metric"], item["detail"])
        if key in seen:
            continue
        seen.add(key)
        lines.append(f"  {item['metric']}: {item['detail']}")

    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Offline delivery evaluation against the organizer expected CSV"
    )
    parser.add_argument("--expected", required=True,
                        help="path to organizer Expected Output (Delivery Format) CSV")
    parser.add_argument("--generated", required=True,
                        help="path to our generated delivery CSV")
    parser.add_argument("--mpns", default="",
                        help="comma-separated extra MPNs for per-row isolation checks")
    parser.add_argument("--json-out", default="",
                        help="optional path to write the JSON report")
    args = parser.parse_args(argv)

    extra_mpns = [m.strip() for m in args.mpns.split(",") if m.strip()]
    try:
        report = run_evaluation(args.expected, args.generated, extra_mpns)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    print(render_text(report))
    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as handle:
            json.dump(asdict(report), handle, indent=2, ensure_ascii=False)
        print(f"JSON report written to {args.json_out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
