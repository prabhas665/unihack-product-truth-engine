"""POST /api/evaluation/run: run the evaluation harness (Step 14B, D3).

Runs the pipeline over the official input CSV (offline by default, ``--live``
via the request body), scores against the expected-output benchmark, and
returns an ``EvaluationReport``. The report is also written to backend/reports.

SECURITY: the endpoint can read arbitrary CSV paths, write reports and trigger
paid live pipeline runs, so it is locked down:

- ``EVALUATION_API_TOKEN`` must be set; requests need ``Authorization: Bearer
  <token>``. When the token is unset the endpoint is disabled (403).
- input/expected/report paths must resolve INSIDE the repository; absolute
  paths elsewhere are rejected.
"""

from __future__ import annotations

import hmac
from pathlib import Path

from fastapi import APIRouter, Body, HTTPException, Request

from app.config import settings
from app.evaluation.runner import (
    DEFAULT_EXPECTED,
    DEFAULT_INPUT,
    DEFAULT_REPORT_DIR,
    EvaluationReport,
    run_evaluation,
)
from app.unihack.paths import repo_root

router = APIRouter(prefix="/api", tags=["evaluation"])


def _require_token(request: Request) -> None:
    configured = settings.evaluation_api_token
    if not configured:
        raise HTTPException(
            status_code=403,
            detail="evaluation endpoint disabled: EVALUATION_API_TOKEN not set",
        )
    supplied = request.headers.get("authorization", "")
    if not supplied.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="missing bearer token")
    candidate = supplied[len("Bearer "):].strip()
    if not hmac.compare_digest(candidate, configured):
        raise HTTPException(status_code=401, detail="invalid token")


def _confine_path(value: str | None, default: str, name: str) -> str:
    """Resolve the path and require it to stay inside the repository."""
    root = repo_root().resolve()
    raw = value or default
    try:
        resolved = Path(raw).expanduser().resolve()
    except (OSError, RuntimeError) as exc:
        raise HTTPException(
            status_code=422, detail=f"{name} cannot be resolved"
        ) from exc
    try:
        resolved.relative_to(root)
    except ValueError:
        raise HTTPException(
            status_code=422,
            detail=f"{name} must resolve inside the repository ({root})",
        )
    return str(resolved)


@router.post("/evaluation/run", response_model=EvaluationReport)
def run_evaluation_endpoint(
    request: Request,
    input_path: str | None = Body(default=None),
    expected_path: str | None = Body(default=None),
    live: bool = Body(default=False),
    limit: int | None = Body(default=None),
    out_dir: str | None = Body(default=None),
) -> EvaluationReport:
    _require_token(request)
    return run_evaluation(
        _confine_path(input_path, DEFAULT_INPUT, "input_path"),
        _confine_path(expected_path, DEFAULT_EXPECTED, "expected_path"),
        live=live,
        limit=limit,
        report_dir=_confine_path(out_dir, DEFAULT_REPORT_DIR, "out_dir"),
    )