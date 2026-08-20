"""POST /api/batch: enrich a client-supplied list of products.

Runs the real enrichment pipeline (settings-configured providers) for every
supplied row, writes one combined 252-column delivery CSV to ``data/batch/``,
persists the run as a ``Job`` with one ``ProductRecordModel`` per row (payload
= the full EnrichmentResult), and returns a reviewable summary plus a download
URL.

The batch endpoint no longer reads the official UniHack input CSV; callers
supply their own rows (as little as an MPN). Every batch is bounded: the row
count must be at most ``settings.batch_max_rows``; ``mpns`` (exact semantic
MPNs) or ``start``/``limit`` slice the supplied rows. Oversized or empty
requests are rejected with HTTP 422 - an unbounded run is impossible and user
requests are never silently truncated. Row-level failures do not abort the run:
the failed row is recorded with status ``failed`` and a sanitized review reason
(exception class name only, no args/stack/secrets), gets a blank delivery row
(no fabrication, keeps 1:1 alignment with the supplied rows), and the remaining
rows keep processing. Like /api/enrich, the real retrieval/LLM providers run
when the endpoint is actually called; tests override ``get_batch_factory`` to
stay fully offline.
"""

from __future__ import annotations

import csv
import io
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field, model_validator
from sqlalchemy.orm import Session

from app.config import settings
from app.core.domain import ProcessingStatus
from app.db.database import get_session
from app.db.models import Job, ProductRecordModel
from app.pipeline.enrichment import (
    EnrichmentRequest,
    EnrichmentResult,
    EnrichmentService,
)
from app.unihack.models import DeliveryRow
from app.unihack.parser import (
    INPUT_COLUMNS,
    UniHackInputError,
    UniHackInputParser,
)
from app.unihack.schema import DeliverySchema
from app.unihack.writer import DeliveryCsvWriter

# Persist batch CSVs under the runtime data dir (configurable DATA_DIR so a
# deployed service can keep downloads on a persistent volume). Falls back to
# the repo-root data/ directory when DATA_DIR is unset.
BATCH_DIR = settings.runtime_data_dir() / "batch"

router = APIRouter(prefix="/api", tags=["batch"])


class BatchInputRow(BaseModel):
    """One product row supplied by the caller (as little as an MPN)."""

    Mfg_Part_Num: str = ""
    Part_Desc: str = ""
    E1_Brand: str = ""
    Unilog_Brand: str = ""
    DIB_Brand: str = ""
    Part_Manuf: str = ""


class BatchRequest(BaseModel):
    """Rows to enrich (bounded, never unbounded).

    ``rows`` carries the caller's data (MPN + optional attributes). ``mpns``
    selects a subset by exact semantic MPN; ``start``/``limit`` slice the list.
    The total row count must be at most ``settings.batch_max_rows`` and the
    request must contain at least one row.
    """

    rows: list[BatchInputRow] = Field(default_factory=list)
    start: int = Field(default=0, ge=0)
    limit: int = Field(default=0, ge=0)
    mpns: list[str] | None = Field(default=None)

    @model_validator(mode="after")
    def _validate_bounds(self) -> "BatchRequest":
        if not self.rows:
            raise ValueError("batch requires at least one row")
        if len(self.rows) > settings.batch_max_rows:
            raise ValueError(
                f"rows has {len(self.rows)} entries; the maximum is "
                f"{settings.batch_max_rows}"
            )
        if self.mpns is not None:
            self.mpns = [mpn for mpn in self.mpns if mpn.strip()] or None
            if self.mpns and len(self.mpns) > settings.batch_max_rows:
                raise ValueError(
                    f"mpns list has {len(self.mpns)} entries; the maximum "
                    f"is {settings.batch_max_rows}"
                )
        elif self.limit:
            if self.limit < 1 or self.limit > settings.batch_max_rows:
                raise ValueError(
                    f"limit must be between 1 and {settings.batch_max_rows} "
                    f"(got {self.limit})"
                )
        return self

    def parsed_rows(self) -> list:
        """Parse ``rows`` through the real input parser (placeholders, errors).

        The same validation used for the official dataset is applied to
        caller-supplied rows; malformed rows surface via ``row_errors`` and
        are skipped, never fabricated.
        """
        lines = [",".join(INPUT_COLUMNS)]
        for row in self.rows:
            lines.append(
                ",".join(
                    field.replace(",", " ").replace('"', " ")
                    for field in (
                        row.Mfg_Part_Num,
                        row.Part_Desc,
                        row.E1_Brand,
                        row.Unilog_Brand,
                        row.DIB_Brand,
                        row.Part_Manuf,
                    )
                )
            )
        try:
            return UniHackInputParser().parse_text("\n".join(lines)).rows
        except UniHackInputError as exc:
            raise ValueError(f"invalid batch rows: {exc}") from exc

    def select(self, rows: list) -> list:
        """Rows to enrich for this request (empty -> nothing selected)."""
        if self.mpns is not None:
            wanted = {mpn.strip().lower() for mpn in self.mpns}
            return [
                row
                for row in rows
                if (row.mfg_part_num_value or "").strip().lower() in wanted
            ]
        if self.limit:
            end = self.start + self.limit
            return list(rows[self.start : end])
        return rows


class BatchRowResult(BaseModel):
    row_id: int
    mfg_part_num: str
    processing_status: str
    delivery_columns: int
    review_reasons: list[str] = Field(default_factory=list)
    description_variants: int = 0


class BatchResult(BaseModel):
    requested: int
    processed: int
    status_counts: dict[str, int] = Field(default_factory=dict)
    rows: list[BatchRowResult] = Field(default_factory=list)
    delivery_file: str = ""
    download_url: str = ""
    job_id: int | None = None


def get_batch_factory() -> Callable[[], EnrichmentService]:
    """Build the real pipeline service per batch run (settings-driven)."""

    def factory() -> EnrichmentService:
        return EnrichmentService()

    return factory


def _delivery_schema() -> DeliverySchema:
    return DeliverySchema.frozen()


def _description_variant_count(result: EnrichmentResult) -> int:
    descriptions = result.product.descriptions if result.product else None
    if descriptions is None:
        return 0
    return sum(
        1
        for value in (
            descriptions.product_title,
            descriptions.short_description,
            descriptions.mobile_description,
            descriptions.invoice_description,
            descriptions.long_description,
            descriptions.retail_description,
            descriptions.marketing_description,
        )
        if value
    )


def _row_report(result: EnrichmentResult, row_id: int) -> BatchRowResult:
    return BatchRowResult(
        row_id=row_id,
        mfg_part_num=result.input_row.mfg_part_num_value or "",
        processing_status=result.processing.status.value,
        delivery_columns=result.delivery.column_count,
        review_reasons=list(result.review_reasons),
        description_variants=_description_variant_count(result),
    )


def _blank_delivery_row(schema: DeliverySchema) -> DeliveryRow:
    """Honest placeholder for a failed row: no fabricated data, exact width.

    Keeping the row in the CSV preserves 1:1 alignment with the dataset
    selection while signaling that nothing was produced for it.
    """

    return DeliveryRow(values=[""] * schema.count, notes=[])


def _bounded_payload(result: EnrichmentResult) -> str:
    """Serialized result with evidence text capped for SQLite growth safety.

    Traceability is preserved: evidence ids, URLs and every extracted
    attribute quote stay intact; only the reviewable raw text body of each
    evidence record is capped to settings.batch_payload_evidence_cap_chars.
    """

    data = result.model_dump(mode="json")
    cap = settings.batch_payload_evidence_cap_chars
    for evidence in data.get("evidence", []):
        text = evidence.get("text")
        if isinstance(text, str) and len(text) > cap:
            evidence["text"] = text[:cap] + "\n... [truncated by persistence]"
    return json.dumps(data)


def _job_status(status_counts: dict[str, int]) -> str:
    if not status_counts:
        return "completed"
    statuses = set(status_counts)
    if statuses == {ProcessingStatus.COMPLETED.value}:
        return "completed"
    if statuses == {ProcessingStatus.FAILED.value}:
        return "failed"
    # Mixed completed/failed (or any needs_review) runs need human review.
    return "needs_review"


@router.post("/batch", response_model=BatchResult)
def run_batch(
    request: BatchRequest,
    service_factory: Callable[[], EnrichmentService] = Depends(get_batch_factory),
    session: Session = Depends(get_session),
) -> BatchResult:
    parsed = request.parsed_rows()
    selected = request.select(parsed)

    service = service_factory()
    schema = _delivery_schema()

    # Crash-safe batch: the CSV header is written once up front and the Job
    # row is created before any enrichment; every completed row is then
    # persisted (DB record + CSV line) IMMEDIATELY, so an interrupted run
    # never loses finished rows and the Job stays "running" (clearly
    # unfinished) instead of silently vanishing.
    BATCH_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    filename = f"batch-{stamp}-{uuid.uuid4().hex[:8]}.csv"
    csv_path = BATCH_DIR / filename
    writer = DeliveryCsvWriter(schema)
    writer.write_header(csv_path)

    job = Job(
        kind="batch",
        status="running",
        created_at=datetime.utcnow(),
    )
    session.add(job)
    session.flush()

    reports: list[BatchRowResult] = []
    status_counts: dict[str, int] = {}
    committed_rows = 0

    try:
        for row in selected:
            try:
                result = service.run(EnrichmentRequest.from_row(row))
            except Exception as exc:  # noqa: BLE001 - row isolation: keep going
                # Never expose args, stack traces or secrets; the exception
                # class name alone is enough to route a human to the logs.
                reason = f"unexpected row failure: {type(exc).__name__}"
                status = ProcessingStatus.FAILED.value
                session.add(
                    ProductRecordModel(
                        job_id=job.id,
                        manufacturer="",
                        brand="",
                        part_number=row.mfg_part_num_value or "",
                        description=row.part_desc_value or "",
                        status=status,
                        quality_score=0.0,
                        payload=json.dumps(
                            {
                                "row_error": {
                                    "type": type(exc).__name__,
                                    "reason": reason,
                                }
                            }
                        ),
                    )
                )
                reports.append(
                    BatchRowResult(
                        row_id=row.row_id,
                        mfg_part_num=row.mfg_part_num_value or "",
                        processing_status=status,
                        delivery_columns=0,
                        review_reasons=[reason],
                    )
                )
                status_counts[status] = status_counts.get(status, 0) + 1
                session.commit()
                committed_rows += 1
                writer.append_row(csv_path, _blank_delivery_row(schema))
                continue
            product = result.product
            session.add(
                ProductRecordModel(
                    job_id=job.id,
                    manufacturer=(
                        product.identity.manufacturer if product else ""
                    ),
                    brand=product.identity.brand if product else "",
                    part_number=result.input_row.mfg_part_num_value or "",
                    description=result.input_row.part_desc_value or "",
                    status=result.processing.status.value,
                    quality_score=result.quality.overall,
                    payload=_bounded_payload(result),
                )
            )
            report = _row_report(result, row.row_id)
            reports.append(report)
            status_counts[report.processing_status] = (
                status_counts.get(report.processing_status, 0) + 1
            )
            session.commit()
            committed_rows += 1
            writer.append_row(
                csv_path,
                DeliveryRow(
                    values=list(result.delivery.values),
                    notes=list(result.delivery.notes),
                ),
            )

        job.status = _job_status(status_counts)
        session.commit()
    except Exception:
        session.rollback()
        if committed_rows == 0:
            # Nothing was persisted: never leave an orphan file behind for a
            # run that produced no committed rows.
            csv_path.unlink(missing_ok=True)
        raise

    return BatchResult(
        requested=len(selected),
        processed=len(selected),
        status_counts=status_counts,
        rows=reports,
        delivery_file=filename,
        download_url=f"/api/downloads/{filename}",
        job_id=job.id,
    )