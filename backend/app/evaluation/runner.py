"""Evaluation harness: run the pipeline, score against the benchmark (Step 14B, D3).

Offline (default): ``EnrichmentService(providers=[], llm_client=None)`` so no
network/LLM is touched - discovery yields zero candidates, extraction and
description generation are skipped, and the verified-only identity mapping +
input passthrough still exercise every derived cell. ``--live`` uses the
settings-configured providers and a real LLM client.

Produces an ``EvaluationReport`` (JSON-serializable) with: placeholder-leak
counts over derived cells, identity exact-match rate on the benchmark MPNs,
description rule pass rates, an invoice-length histogram, and per-benchmark-row
cell comparisons.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel, Field

from app.descriptions.rules import INVOICE_MAX, MOBILE_MAX, MOBILE_MIN
from app.evaluation.benchmark import (
    SAMPLED_DESC_COLUMNS,
    SAMPLED_IDENTITY_COLUMNS,
    is_leak,
    load_expected,
    load_input_rows,
    normalize_expected_cell,
)
from app.identity.mapping import VerifiedBrandLookup
from app.pipeline.enrichment import EnrichmentRequest, EnrichmentService
from app.unihack.mapper import UniHackDeliveryMapper
from app.unihack.schema import DeliverySchema

# Dev/evaluation-only fixtures: copies of the official files under
# backend/tests/fixtures so the harness runs offline. These are NOT read by any
# production runtime code path.
REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
FIXTURE_DIR = REPO_ROOT / "backend" / "tests" / "fixtures"
DEFAULT_INPUT = FIXTURE_DIR / "Unihack_ Sample Dataset - Input.csv"
DEFAULT_EXPECTED = FIXTURE_DIR / "Unihack_ Expected Output - Delivery Format.csv"
DEFAULT_REPORT_DIR = REPO_ROOT / "backend" / "reports"


class CellComparison(BaseModel):
    column: str
    expected: str
    actual: str
    # None when the cell was skipped (e.g. descriptions offline).
    match: bool | None = None


class RowResult(BaseModel):
    mpn: str
    exact_match: bool
    leak: bool
    comparisons: list[CellComparison] = Field(default_factory=list)


class RowDetail(BaseModel):
    """Per-row live pipeline metrics (Step 14C extension of D3).

    Captures the stages the summary report previously omitted: discovery,
    manufacturer-source acceptance, retrieval, extraction, validation, and
    description generation. Empty/absent when a stage was skipped (offline or
    when no source candidate passed SourcePolicy).
    """

    mpn: str = ""
    processing_status: str = ""
    discovery_discovered: int = 0
    discovery_allowed: int = 0
    discovery_rejected: int = 0
    discovery_provider_errors: list[str] = Field(default_factory=list)
    retrieval_evidence_count: int = 0
    extraction_accepted: int = 0
    extraction_rejected: int = 0
    validation_counts: dict[str, int] = Field(default_factory=dict)
    descriptions_generated: list[str] = Field(default_factory=list)
    identity_manufacturer: str = ""
    identity_brand: str = ""
    identity_trade_name: str = ""
    identity_provenance: str = ""
    delivery_columns: int = 0
    review_reasons: list[str] = Field(default_factory=list)


def _validation_counts(val) -> dict[str, int]:
    if val is None:
        return {}
    counts = getattr(val, "counts", None)
    if isinstance(counts, dict):
        return {str(k): int(v) for k, v in counts.items()}
    out: dict[str, int] = {}
    for item in val if hasattr(val, "__iter__") else []:
        outcome = getattr(getattr(item, "outcome", None), "value", None)
        if outcome is None:
            outcome = getattr(getattr(item, "status", None), "value", None)
        out[str(outcome or "unknown")] = out.get(str(outcome or "unknown"), 0) + 1
    return out


def _build_row_detail(row, result: "EnrichmentResult") -> RowDetail:
    disc = result.discovery
    discovered = allowed = rejected = 0
    provider_errors: list[str] = []
    if disc is not None:
        candidates = list(getattr(disc, "candidates", []) or [])
        rejects = list(getattr(disc, "rejected", []) or [])
        discovered = len(candidates) + len(rejects)
        allowed = sum(
            1
            for c in candidates
            if getattr(getattr(c, "status", None), "value", None) == "allowed"
        )
        rejected = discovered - allowed
        provider_errors = [
            f"{e.provider_name}:{e.error_kind}:{e.message}"
            for e in (getattr(disc, "provider_errors", []) or [])
        ]

    evidence = result.evidence or []
    ext = result.extraction
    extraction_accepted = len(getattr(ext, "attributes", []) or []) if ext else 0
    extraction_rejected = len(getattr(ext, "rejected", []) or []) if ext else 0

    product = result.product
    identity = getattr(product, "identity", None) if product else None
    descriptions = getattr(product, "descriptions", None) if product else None
    descriptions_generated = (
        [k for k, v in descriptions.model_dump().items() if v] if descriptions else []
    )
    provenance = getattr(identity, "identity_provenance", None) if identity else None

    return RowDetail(
        mpn=(row.mfg_part_num or "").strip(),
        processing_status=(
            getattr(result.processing.status, "value", str(result.processing.status))
            if result.processing
            else ""
        ),
        discovery_discovered=discovered,
        discovery_allowed=allowed,
        discovery_rejected=rejected,
        discovery_provider_errors=provider_errors,
        retrieval_evidence_count=len(evidence),
        extraction_accepted=extraction_accepted,
        extraction_rejected=extraction_rejected,
        validation_counts=_validation_counts(result.validation),
        descriptions_generated=descriptions_generated,
        identity_manufacturer=(
            (getattr(identity, "verified_manufacturer", None)
             or getattr(identity, "manufacturer", None)
             or "")
            if identity
            else ""
        ),
        identity_brand=(
            (getattr(identity, "verified_brand", None)
             or getattr(identity, "brand", None)
             or "")
            if identity
            else ""
        ),
        identity_trade_name=(
            getattr(identity, "verified_trade_name", None) or "" if identity else ""
        ),
        identity_provenance=provenance or "",
        delivery_columns=len(getattr(result.delivery, "values", []) or []),
        review_reasons=list(result.review_reasons or []),
    )


class EvaluationReport(BaseModel):
    generated_at: str
    mode: str
    rows_total: int
    rows_evaluated: int
    identity_exact_matches: int
    identity_exact_match_rate: float
    placeholder_leak_rows: int
    placeholder_leak_count: int
    invoice_rule_total: int
    invoice_rule_passed: int
    mobile_rule_total: int
    mobile_rule_passed: int
    invoice_rule_pass_rate: float = 0.0
    mobile_rule_pass_rate: float = 0.0
    invoice_length_histogram: dict[str, int] = Field(default_factory=dict)
    benchmark: list[RowResult] = Field(default_factory=list)
    rows_detail: list[RowDetail] = Field(default_factory=list)
    report_path: str = ""


def build_service(live: bool) -> EnrichmentService:
    if live:
        return EnrichmentService()
    return EnrichmentService(
        providers=[],
        llm_client=None,
        verified_lookup=VerifiedBrandLookup.default(),
    )


def _invoice_compliant(value: str) -> bool:
    if not value:
        return False
    stripped = value.replace("IN.", "").replace("FT.", "")
    return (
        len(value) <= INVOICE_MAX
        and value == value.upper()
        and "," not in value
        and "." not in stripped
    )


def _mobile_compliant(value: str) -> bool:
    if not value:
        return False
    return MOBILE_MIN <= len(value) <= MOBILE_MAX


def run_evaluation(
    input_path: str | Path = DEFAULT_INPUT,
    expected_path: str | Path | None = DEFAULT_EXPECTED,
    *,
    live: bool = False,
    limit: int | None = None,
    report_dir: str | Path = DEFAULT_REPORT_DIR,
) -> EvaluationReport:
    rows = load_input_rows(str(input_path))
    if limit is not None:
        rows = rows[:limit]
    expected = load_expected(str(expected_path)) if expected_path else {}

    schema = DeliverySchema.frozen()
    mapper = UniHackDeliveryMapper(schema)
    service = build_service(live)

    derived_index = {
        name: schema.index_of(name)
        for name in schema.headers
        if name not in {
            "Mfg_Part_Num",
            "Part_Desc",
            "E1_Brand",
            "Unilog_Brand",
            "DIB_Brand",
            "Part_Manuf",
        }
    }

    report = EvaluationReport(
        generated_at=datetime.now(timezone.utc).isoformat(),
        mode="live" if live else "offline",
        rows_total=len(rows),
        rows_evaluated=0,
        identity_exact_matches=0,
        identity_exact_match_rate=0.0,
        placeholder_leak_rows=0,
        placeholder_leak_count=0,
        invoice_rule_total=0,
        invoice_rule_passed=0,
        mobile_rule_total=0,
        mobile_rule_passed=0,
    )

    for row in rows:
        request = EnrichmentRequest.from_row(row)
        result = service.run(request)
        delivery = result.delivery
        report.rows_evaluated += 1

        row_leak = False
        for name, idx in derived_index.items():
            if is_leak(delivery.values[idx]):
                row_leak = True
                report.placeholder_leak_count += 1
        if row_leak:
            report.placeholder_leak_rows += 1

        invoice = delivery.values[schema.index_of("INVOICE_DESC")]
        mobile = delivery.values[schema.index_of("MOBILE_DESC")]
        if invoice:
            report.invoice_rule_total += 1
            if _invoice_compliant(invoice):
                report.invoice_rule_passed += 1
        if mobile:
            report.mobile_rule_total += 1
            if _mobile_compliant(mobile):
                report.mobile_rule_passed += 1
        report.invoice_length_histogram[str(len(invoice))] = (
            report.invoice_length_histogram.get(str(len(invoice)), 0) + 1
        )

        report.rows_detail.append(_build_row_detail(row, result))

        mpn = (row.mfg_part_num or "").strip()
        if mpn and mpn in expected:
            exp = expected[mpn]
            comparisons: list[CellComparison] = []
            columns = SAMPLED_IDENTITY_COLUMNS + (
                SAMPLED_DESC_COLUMNS if live else []
            )
            exact = True
            for column in columns:
                expected_val = normalize_expected_cell(column, exp.get(column, ""))
                actual_val = delivery.values[schema.index_of(column)]
                if column in SAMPLED_DESC_COLUMNS:
                    # Descriptions require a live LLM; scored only in live mode.
                    comparisons.append(
                        CellComparison(
                            column=column,
                            expected=expected_val,
                            actual=actual_val,
                            match=None,
                        )
                    )
                    continue
                match = expected_val == actual_val
                exact = exact and match
                comparisons.append(
                    CellComparison(
                        column=column,
                        expected=expected_val,
                        actual=actual_val,
                        match=match,
                    )
                )
            if exact:
                report.identity_exact_matches += 1
            report.benchmark.append(
                RowResult(
                    mpn=mpn, exact_match=exact, leak=row_leak, comparisons=comparisons
                )
            )

    if report.rows_evaluated:
        denom = max(1, len(report.benchmark))
        report.identity_exact_match_rate = report.identity_exact_matches / denom
    report.invoice_rule_pass_rate = (
        report.invoice_rule_passed / report.invoice_rule_total
        if report.invoice_rule_total
        else 0.0
    )
    report.mobile_rule_pass_rate = (
        report.mobile_rule_passed / report.mobile_rule_total
        if report.mobile_rule_total
        else 0.0
    )

    _write_report(report, report_dir)
    return report


def _write_report(report: EvaluationReport, report_dir: str | Path) -> None:
    report_dir = Path(report_dir)
    report_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = report_dir / f"evaluation_{report.mode}_{stamp}.json"
    report.report_path = str(path)
    path.write_text(json.dumps(report.model_dump(), indent=2), encoding="utf-8")
