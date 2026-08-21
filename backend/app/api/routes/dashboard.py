"""GET /api/dashboard: persisted-enrichment statistics + last batch overview.

``database`` aggregates the persistent store (total records, counts by status,
needs_review, recent MPNs) - no dependency on the official input CSV.
``last_batch_run`` comes from the persisted jobs table. ``compliance`` is
derived from the most recent evaluation report (None before any run).
"""

from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import desc, func
from sqlalchemy.orm import Session

from app.db.database import get_session
from app.db.models import Job, ProductRecordModel
from app.evaluation.runner import DEFAULT_REPORT_DIR

import glob
import json


class DatabaseStats(BaseModel):
    total_records: int = 0
    by_status: dict[str, int] = Field(default_factory=dict)
    needs_review: int = 0
    recent_mpns: list[str] = Field(default_factory=list)


class BatchRunSummary(BaseModel):
    job_id: int
    created_at: datetime
    status: str
    record_count: int
    status_counts: dict[str, int] = Field(default_factory=dict)


class ComplianceSummary(BaseModel):
    placeholder_leak_rows: int | None = None
    invoice_rule_pass_rate: float | None = None
    mobile_rule_pass_rate: float | None = None
    last_report_path: str | None = None


class DashboardResponse(BaseModel):
    database: DatabaseStats
    last_batch_run: BatchRunSummary | None = None
    compliance: ComplianceSummary | None = None


router = APIRouter(prefix="/api", tags=["dashboard"])


def _database_stats(session: Session) -> DatabaseStats:
    total = session.query(func.count()).select_from(
        ProductRecordModel
    ).scalar() or 0
    by_status = dict(
        session.query(ProductRecordModel.status, func.count())
        .group_by(ProductRecordModel.status)
        .all()
    )
    needs_review = by_status.get("needs_review", 0)
    recent = list(dict.fromkeys(
        row.part_number
        for row in (
            session.query(ProductRecordModel)
            .order_by(desc(ProductRecordModel.id))
            .limit(10)
            .all()
        )
        if row.part_number
    ))
    return DatabaseStats(
        total_records=total,
        by_status=by_status,
        needs_review=needs_review,
        recent_mpns=recent,
    )


def _last_batch_run(session: Session) -> BatchRunSummary | None:
    job = (
        session.query(Job)
        .filter(Job.kind == "batch")
        .order_by(Job.id.desc())
        .first()
    )
    if job is None:
        return None
    counts = dict(
        session.query(ProductRecordModel.status, func.count())
        .filter(ProductRecordModel.job_id == job.id)
        .group_by(ProductRecordModel.status)
        .all()
    )
    return BatchRunSummary(
        job_id=job.id,
        created_at=job.created_at,
        status=job.status,
        record_count=sum(counts.values()),
        status_counts=counts,
    )


def _latest_report_path() -> Path | None:
    matches = glob.glob(str(DEFAULT_REPORT_DIR / "evaluation_*.json"))
    if not matches:
        return None
    return Path(max(matches, key=lambda p: Path(p).stat().st_mtime))


def _compliance_summary() -> ComplianceSummary:
    latest = _latest_report_path()
    if latest is None:
        return ComplianceSummary()
    try:
        with latest.open(encoding="utf-8") as handle:
            data = json.load(handle)
    except (json.JSONDecodeError, OSError):
        return ComplianceSummary()
    leak = data.get("placeholder_leak_rows")
    inv_total = data.get("invoice_rule_total") or 0
    inv_pass = data.get("invoice_rule_passed") or 0
    mob_total = data.get("mobile_rule_total") or 0
    mob_pass = data.get("mobile_rule_passed") or 0
    invoice_rate = (inv_pass / inv_total) if inv_total else None
    mobile_rate = (mob_pass / mob_total) if mob_total else None
    return ComplianceSummary(
        placeholder_leak_rows=leak,
        invoice_rule_pass_rate=invoice_rate,
        mobile_rule_pass_rate=mobile_rate,
        last_report_path=latest.name,
    )


@router.get("/dashboard", response_model=DashboardResponse)
def dashboard(session: Session = Depends(get_session)) -> DashboardResponse:
    return DashboardResponse(
        database=_database_stats(session),
        last_batch_run=_last_batch_run(session),
        compliance=_compliance_summary(),
    )
