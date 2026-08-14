"""POST /api/evaluation/run: run the evaluation harness (Step 14B, D3).

Runs the pipeline over the official input CSV (offline by default, ``--live``
via the request body), scores against the expected-output benchmark, and
returns an ``EvaluationReport``. The report is also written to backend/reports.
"""

from __future__ import annotations

from fastapi import APIRouter, Body

from app.evaluation.runner import (
    DEFAULT_EXPECTED,
    DEFAULT_INPUT,
    DEFAULT_REPORT_DIR,
    EvaluationReport,
    run_evaluation,
)

router = APIRouter(prefix="/api", tags=["evaluation"])


@router.post("/evaluation/run", response_model=EvaluationReport)
def run_evaluation_endpoint(
    input_path: str | None = Body(default=None),
    expected_path: str | None = Body(default=None),
    live: bool = Body(default=False),
    limit: int | None = Body(default=None),
    out_dir: str | None = Body(default=None),
) -> EvaluationReport:
    return run_evaluation(
        input_path or DEFAULT_INPUT,
        expected_path or DEFAULT_EXPECTED,
        live=live,
        limit=limit,
        report_dir=out_dir or DEFAULT_REPORT_DIR,
    )
