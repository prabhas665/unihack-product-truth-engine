"""Repository service for persistent product intelligence (Step 10B).

Wraps every database access to ``product_records`` behind a small, typed
service so API routes never write raw SQL or reach into the ORM directly.

Design choices that matter for callers:

* **MPN normalization.** Lookups, persisted ``part_number`` values, and
  response identities use one trimmed uppercase canonical representation.
* **No silent merging.** Duplicate MPNs in the UniHack dataset are real and
  can belong to different manufacturer/description contexts. ``find_by_mpn``
  and ``find_fresh_records_by_mpn`` therefore return ALL matches ordered by
  recency; the API is responsible for surfacing them without collapsing.
* **Freshness verdict.** ``find_fresh_by_mpn`` selects the most recent
  *successful* record (``completed`` or ``needs_review``) with a non-null
  ``last_enriched_at`` and returns a verdict of ``fresh`` (within
  ``freshness_days``), ``stale`` (older than that) or ``not_found``.
* **Successful-only saves.** ``save_enrichment`` is the single entry point
  for writing the Step 10B structured fields and is called ONLY after a
  pipeline run produces a real ``EnrichmentResult``. Failed rows are saved
  with ``save_failed_record`` and only carry the legacy ``payload`` plus
  the Step 10B defaults.
* **Secret-free persistence.** No API keys, Authorization headers or
  provider settings are ever serialized into the stored JSON columns or the
  legacy payload; the evidence ``text`` is capped to
  ``settings.batch_payload_evidence_cap_chars`` to keep SQLite bounded.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Iterable

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.config import settings
from app.core.domain.common import utcnow
from app.db.models import ProductRecordModel
from app.pipeline.enrichment import (
    EnrichmentResult,
    canonicalize_mpn,
    require_enrichment_identity,
)


class FreshnessVerdict(str, Enum):
    """How a stored product matches the configured freshness window."""

    FRESH = "fresh"
    STALE = "stale"
    NOT_FOUND = "not_found"


# Statuses that count as a "successful" run for the freshness / selection
# policy. Failed runs are intentionally excluded from ``find_fresh_by_mpn``
# because they would just route the caller straight back into the pipeline.
SUCCESS_STATUSES: frozenset[str] = frozenset({"completed", "needs_review"})


def normalize_mpn(value: str | None) -> str:
    """Return the canonical MPN used by lookup, storage, and API output."""
    return canonicalize_mpn(value)


def _mpn_match_clause(column, target: str):
    """Build a case-insensitive, whitespace-trimmed MPN match clause.

    SQLAlchemy parameterizes ``target`` so this stays injection-safe.
    """
    return func.lower(func.trim(column)) == func.lower(target.strip())


@dataclass(frozen=True)
class RunSummary:
    """One row of the recent-runs listing."""

    record_id: int
    job_id: int
    part_number: str
    status: str
    last_enriched_at: datetime | None
    source_count: int
    evidence_count: int
    attribute_count: int


@dataclass(frozen=True)
class PersistedStats:
    """Aggregate stats for the dashboard's persistent-intelligence block."""

    total_persisted_products: int
    fresh_products: int
    stale_products: int
    successful_enrichments: int
    needs_review_enrichments: int
    failed_enrichments: int
    reused_from_database_count: int


def _safe_json_dumps(value: Any) -> str:
    """Serialize ``value`` to JSON; on error, fall back to ``[]`` / ``{}``.

    The fallback prevents a single malformed value from blocking a save; the
    next successful run will overwrite it.
    """

    try:
        return json.dumps(value, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        if isinstance(value, dict):
            return "{}"
        return "[]"


def _safe_json_loads(value: str | None, default: Any) -> Any:
    """Deserialize ``value``; return ``default`` on missing or invalid input."""
    if not value:
        return default
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


def _cap_evidence_text(text: str) -> str:
    """Cap the evidence body stored alongside the record.

    Mirrors ``app.api.routes.batch._bounded_payload`` so the persistent
    fields and the legacy payload stay aligned in size.
    """
    cap = settings.batch_payload_evidence_cap_chars
    if not isinstance(text, str) or len(text) <= cap:
        return text or ""
    return text[:cap] + "\n... [truncated by persistence]"


def _serialize_sources(result: EnrichmentResult) -> list[dict[str, Any]]:
    """Build the ``sources_json`` array from the discovery result."""
    sources: list[dict[str, Any]] = []
    for candidate in result.discovery.candidates:
        sources.append(
            {
                "url": candidate.url,
                "source_type": candidate.source_type.value
                if hasattr(candidate.source_type, "value")
                else str(candidate.source_type),
                "trust_level": str(candidate.trust_level or ""),
                "last_retrieved": utcnow().isoformat(),
            }
        )
    return sources


def _serialize_evidence(result: EnrichmentResult) -> dict[str, dict[str, Any]]:
    """Build the ``evidence_json`` map keyed by evidence id."""
    evidence: dict[str, dict[str, Any]] = {}
    for record in result.evidence:
        evidence[record.evidence_id] = {
            "url": record.url,
            "source_type": record.source_type.value
            if hasattr(record.source_type, "value")
            else str(record.source_type),
            "title": record.title or "",
            "snippet": "",
            "text": _cap_evidence_text(record.text or ""),
            "retrieved_at": record.retrieved_at.isoformat()
            if record.retrieved_at
            else utcnow().isoformat(),
            "trust_level": "manufacturer_official",
        }
    return evidence


def _serialize_attributes(result: EnrichmentResult) -> dict[str, dict[str, Any]]:
    """Build ``attributes_json`` keyed by attribute name."""
    attributes: dict[str, dict[str, Any]] = {}
    if result.product is None:
        return attributes
    for name, attr in result.product.attributes.items():
        attributes[name] = {
            "raw_value": attr.raw_value or "",
            "value": attr.value or "",
            "unit": attr.unit or "",
            "confidence": float(attr.confidence or 0.0),
            "evidence_refs": list(attr.evidence_refs or []),
            "validation_status": str(attr.status.value)
            if hasattr(attr.status, "value")
            else str(attr.status),
            "review": attr.review.model_dump(mode="json") if attr.review else {},
        }
    return attributes


def _serialize_descriptions(result: EnrichmentResult) -> dict[str, Any]:
    """Build ``descriptions_json`` from the product intelligence record."""
    if result.product is None:
        return {}
    return result.product.descriptions.model_dump(mode="json")


def _serialize_validation(result: EnrichmentResult) -> dict[str, dict[str, Any]]:
    """Build ``validation_json`` keyed by attribute name."""
    validation: dict[str, dict[str, Any]] = {}
    summary = result.validation
    if summary is None:
        return validation
    for attribute in summary.attributes:
        validation[attribute.name] = {
            "outcome": attribute.outcome.value
            if hasattr(attribute.outcome, "value")
            else str(attribute.outcome),
            "messages": [msg.model_dump(mode="json") for msg in attribute.messages],
        }
    return validation


def _append_history(
    record: ProductRecordModel, run_id: str, status: str, counts: dict[str, int]
) -> list[dict[str, Any]]:
    """Append a new entry to ``enrichment_history_json`` and return the list."""
    history = _safe_json_loads(record.enrichment_history_json, [])
    if not isinstance(history, list):
        history = []
    history.append(
        {
            "run_id": run_id,
            "status": status,
            "timestamp": utcnow().isoformat(),
            "source_count": counts.get("source_count", 0),
            "evidence_count": counts.get("evidence_count", 0),
            "attribute_count": counts.get("attribute_count", 0),
        }
    )
    return history


class ProductRepository:
    """Persistence service for product intelligence records.

    Stateless; every method takes the session it needs. The session is
    committed by the caller (route or test) - ``save_*`` only flushes so
    callers can compose multiple operations in a single transaction.
    """

    # ------------------------------------------------------------------
    # Lookups
    # ------------------------------------------------------------------

    def find_by_mpn(
        self, session: Session, mpn: str
    ) -> list[ProductRecordModel]:
        """Return ALL stored records for ``mpn``, newest first.

        Never silently merges. The UniHack dataset contains duplicate MPNs
        that may belong to different manufacturer/description contexts; the
        caller decides which one to display.
        """
        target = normalize_mpn(mpn)
        if not target:
            return []
        records = (
            session.query(ProductRecordModel)
            .filter(_mpn_match_clause(ProductRecordModel.part_number, target))
            .order_by(
                ProductRecordModel.last_enriched_at.desc().nulls_last(),
                ProductRecordModel.id.desc(),
            )
            .all()
        )
        return list(records)

    def find_fresh_records_by_mpn(
        self, session: Session, mpn: str, freshness_days: int
    ) -> list[ProductRecordModel]:
        """Return stored records for ``mpn`` that are still within freshness.

        A record is fresh when ``last_enriched_at >= now - freshness_days``
        AND its status is in ``SUCCESS_STATUSES``. Multiple matches are
        returned in full (no merging).
        """
        target = normalize_mpn(mpn)
        if not target:
            return []
        # A non-positive freshness window disables freshness: nothing counts
        # as fresh, so callers fall back to the dataset (config: "0 disables
        # freshness (everything treated as stale)").
        if freshness_days is not None and int(freshness_days) <= 0:
            return []
        cutoff = utcnow() - timedelta(days=max(int(freshness_days), 0))
        records = (
            session.query(ProductRecordModel)
            .filter(_mpn_match_clause(ProductRecordModel.part_number, target))
            .filter(ProductRecordModel.status.in_(list(SUCCESS_STATUSES)))
            .filter(ProductRecordModel.last_enriched_at.isnot(None))
            .filter(ProductRecordModel.last_enriched_at >= cutoff)
            .order_by(
                ProductRecordModel.last_enriched_at.desc().nulls_last(),
                ProductRecordModel.id.desc(),
            )
            .all()
        )
        return list(records)

    def find_fresh_by_mpn(
        self, session: Session, mpn: str, freshness_days: int
    ) -> tuple[ProductRecordModel | None, FreshnessVerdict]:
        """Return (most_recent_record, freshness_verdict) for ``mpn``.

        Selection policy (documented): prefer the most recent SUCCESSFUL
        record; if none exists, fall back to the most recent record of any
        status; if no record exists, return ``NOT_FOUND``. Stale-vs-fresh is
        decided by comparing ``last_enriched_at`` to ``now - freshness_days``.
        A record with ``last_enriched_at`` of NULL is treated as STALE.
        """
        target = normalize_mpn(mpn)
        if not target:
            return None, FreshnessVerdict.NOT_FOUND
        # A non-positive freshness window disables freshness: there is no fresh
        # match to serve, so the caller falls back to the dataset. The verdict
        # is STALE (config: "0 disables freshness (everything treated as
        # stale)") and no record is returned for the fast path.
        if freshness_days is not None and int(freshness_days) <= 0:
            return None, FreshnessVerdict.STALE
        candidates = (
            session.query(ProductRecordModel)
            .filter(_mpn_match_clause(ProductRecordModel.part_number, target))
            .order_by(
                ProductRecordModel.last_enriched_at.desc().nulls_last(),
                ProductRecordModel.id.desc(),
            )
            .all()
        )
        if not candidates:
            return None, FreshnessVerdict.NOT_FOUND

        # Selection: prefer the most recent SUCCESSFUL record.
        successful = [
            record
            for record in candidates
            if record.status in SUCCESS_STATUSES
        ]
        if successful:
            record = successful[0]
        else:
            record = candidates[0]

        last_seen = record.last_enriched_at
        if last_seen is None:
            return record, FreshnessVerdict.STALE
        # SQLite returns naive UTC datetimes; normalize to aware UTC so the
        # comparison is consistent regardless of the driver.
        if last_seen.tzinfo is None:
            last_seen = last_seen.replace(tzinfo=utcnow().tzinfo)
        cutoff = utcnow() - timedelta(days=max(int(freshness_days), 0))
        if last_seen >= cutoff:
            return record, FreshnessVerdict.FRESH
        return record, FreshnessVerdict.STALE

    def get_product_by_record_id(
        self, session: Session, record_id: int
    ) -> ProductRecordModel | None:
        return (
            session.query(ProductRecordModel)
            .filter(ProductRecordModel.id == record_id)
            .one_or_none()
        )

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------

    def save_enrichment(
        self,
        session: Session,
        result: EnrichmentResult,
        job_id: int,
        run_id: str | None = None,
        freshness_days: int | None = None,
    ) -> ProductRecordModel:
        """Persist a successful ``EnrichmentResult`` and return the new record.

        Steps:
        1. Build the Step 10B structured fields from the result.
        2. Append a new entry to ``enrichment_history_json``.
        3. Persist the legacy ``payload`` for backward compatibility.
        4. Set ``last_enriched_at`` to ``utcnow()``.
        5. Flush so the primary key is available to the caller.

        Raises on a JSON encoding failure: callers are expected to translate
        that into a sanitized server error.
        """
        # Persistence is a second defensive boundary. The API validates too,
        # but repository callers must never be able to store a mixed identity.
        require_enrichment_identity(result)
        rid = run_id or uuid.uuid4().hex
        record = ProductRecordModel(
            job_id=job_id,
            manufacturer=(
                result.product.identity.manufacturer
                if result.product is not None
                else ""
            ),
            brand=(
                result.product.identity.brand if result.product is not None else ""
            ),
            part_number=normalize_mpn(result.input_row.mfg_part_num_value),
            description=result.input_row.part_desc_value or "",
            status=result.processing.status.value,
            quality_score=result.quality.overall,
        )
        self._fill_persistent_fields(record, result, rid, freshness_days)
        session.add(record)
        session.flush()
        return record

    def save_failed_record(
        self,
        session: Session,
        job_id: int,
        mpn: str,
        description: str,
        payload_json: str,
    ) -> ProductRecordModel:
        """Persist a failed row. Only the legacy ``payload`` is set; the
        Step 10B structured fields keep their safe defaults.
        """
        record = ProductRecordModel(
            job_id=job_id,
            manufacturer="",
            brand="",
            part_number=mpn or "",
            description=description or "",
            status="failed",
            quality_score=0.0,
            payload=payload_json or "{}",
        )
        session.add(record)
        session.flush()
        return record

    def update_enrichment_history(
        self,
        session: Session,
        record_id: int,
        run_id: str,
        status: str,
        source_count: int,
        evidence_count: int,
        attribute_count: int,
    ) -> ProductRecordModel | None:
        """Append a new entry to an existing record's history and refresh
        ``last_enriched_at``. Returns the updated record or ``None`` when
        the id is unknown.
        """
        record = self.get_product_by_record_id(session, record_id)
        if record is None:
            return None
        history = _append_history(
            record,
            run_id,
            status,
            {
                "source_count": source_count,
                "evidence_count": evidence_count,
                "attribute_count": attribute_count,
            },
        )
        record.enrichment_history_json = _safe_json_dumps(history)
        record.last_enriched_at = utcnow()
        session.flush()
        return record

    # ------------------------------------------------------------------
    # Recent runs / dashboard
    # ------------------------------------------------------------------

    def list_recent_runs(
        self, session: Session, limit: int = 10
    ) -> list[RunSummary]:
        """List the most recently enriched successful products."""
        records = (
            session.query(ProductRecordModel)
            .filter(ProductRecordModel.last_enriched_at.isnot(None))
            .order_by(ProductRecordModel.last_enriched_at.desc())
            .limit(max(int(limit), 1))
            .all()
        )
        results: list[RunSummary] = []
        for record in records:
            sources = _safe_json_loads(record.sources_json, [])
            evidence = _safe_json_loads(record.evidence_json, {})
            attributes = _safe_json_loads(record.attributes_json, {})
            results.append(
                RunSummary(
                    record_id=record.id,
                    job_id=record.job_id,
                    part_number=record.part_number or "",
                    status=record.status or "",
                    last_enriched_at=record.last_enriched_at,
                    source_count=len(sources) if isinstance(sources, list) else 0,
                    evidence_count=len(evidence) if isinstance(evidence, dict) else 0,
                    attribute_count=len(attributes)
                    if isinstance(attributes, dict)
                    else 0,
                )
            )
        return results

    def dashboard_stats(
        self,
        session: Session,
        freshness_days: int | None = None,
        reused_count: int = 0,
    ) -> PersistedStats:
        """Aggregate persisted-product stats for the dashboard."""
        window = (
            settings.product_cache_freshness_days
            if freshness_days is None
            else int(freshness_days)
        )
        total = session.query(func.count(ProductRecordModel.id)).scalar() or 0

        if window <= 0:
            # Freshness disabled: nothing counts as fresh (config: "0 disables
            # freshness (everything treated as stale)").
            fresh = 0
        else:
            cutoff = utcnow() - timedelta(days=window)
            fresh = (
                session.query(func.count(ProductRecordModel.id))
                .filter(ProductRecordModel.status.in_(list(SUCCESS_STATUSES)))
                .filter(ProductRecordModel.last_enriched_at.isnot(None))
                .filter(ProductRecordModel.last_enriched_at >= cutoff)
                .scalar()
                or 0
            )

        def _count_status(status: str) -> int:
            return (
                session.query(func.count(ProductRecordModel.id))
                .filter(ProductRecordModel.status == status)
                .scalar()
                or 0
            )

        successful = (
            session.query(func.count(ProductRecordModel.id))
            .filter(ProductRecordModel.status == "completed")
            .scalar()
            or 0
        )
        needs_review = (
            session.query(func.count(ProductRecordModel.id))
            .filter(ProductRecordModel.status == "needs_review")
            .scalar()
            or 0
        )
        failed = _count_status("failed")

        stale = max(int(total) - int(fresh), 0)
        return PersistedStats(
            total_persisted_products=int(total),
            fresh_products=int(fresh),
            stale_products=int(stale),
            successful_enrichments=int(successful),
            needs_review_enrichments=int(needs_review),
            failed_enrichments=int(failed),
            reused_from_database_count=int(reused_count),
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _fill_persistent_fields(
        self,
        record: ProductRecordModel,
        result: EnrichmentResult,
        run_id: str,
        freshness_days: int | None,
    ) -> None:
        """Populate the Step 10B columns on ``record``.

        No secrets, no Authorization headers, no provider settings are
        serialized. Evidence text is capped to
        ``settings.batch_payload_evidence_cap_chars``.
        """
        sources = _serialize_sources(result)
        evidence = _serialize_evidence(result)
        attributes = _serialize_attributes(result)
        descriptions = _serialize_descriptions(result)
        validation = _serialize_validation(result)

        # ``last_enriched_at`` + history
        record.last_enriched_at = utcnow()
        history = _append_history(
            record if record.enrichment_history_json else record,
            run_id,
            result.processing.status.value,
            {
                "source_count": len(sources),
                "evidence_count": len(evidence),
                "attribute_count": len(attributes),
            },
        )
        record.enrichment_history_json = _safe_json_dumps(history)
        record.raw_description = (
            result.input_row.part_desc_value or ""
        )
        record.sources_json = _safe_json_dumps(sources)
        record.evidence_json = _safe_json_dumps(evidence)
        record.attributes_json = _safe_json_dumps(attributes)
        record.descriptions_json = _safe_json_dumps(descriptions)
        record.validation_json = _safe_json_dumps(validation)
        # Legacy opaque payload: full EnrichmentResult for backward
        # compatibility and for the ``retrieve_from_db`` rebuild path.
        record.payload = _safe_json_dumps(result.model_dump(mode="json"))
        record.source_freshness_days = (
            int(freshness_days)
            if freshness_days is not None
            else int(settings.product_cache_freshness_days)
        )

    # ------------------------------------------------------------------
    # Helpers exposed for callers that need to rebuild an EnrichmentResult
    # ------------------------------------------------------------------

    @staticmethod
    def record_to_enrichment_payload(record: ProductRecordModel) -> dict[str, Any]:
        """Return the stored record as the dict shape callers can rebuild.

        Includes the legacy ``payload`` (full ``EnrichmentResult`` JSON),
        the Step 10B structured fields, and a small meta block so the API
        can mark the source as ``database`` (and possibly ``stale``).
        """
        return {
            "record_id": record.id,
            "job_id": record.job_id,
            "part_number": record.part_number,
            "manufacturer": record.manufacturer,
            "brand": record.brand,
            "description": record.description,
            "status": record.status,
            "quality_score": record.quality_score,
            "last_enriched_at": record.last_enriched_at.isoformat()
            if record.last_enriched_at
            else None,
            "source_freshness_days": record.source_freshness_days,
            "payload": _safe_json_loads(record.payload, {}),
            "raw_description": record.raw_description,
            "sources": _safe_json_loads(record.sources_json, []),
            "evidence": _safe_json_loads(record.evidence_json, {}),
            "attributes": _safe_json_loads(record.attributes_json, {}),
            "descriptions": _safe_json_loads(record.descriptions_json, {}),
            "validation": _safe_json_loads(record.validation_json, {}),
            "enrichment_history": _safe_json_loads(
                record.enrichment_history_json, []
            ),
        }


def build_enrichment_from_payload(payload: dict[str, Any]) -> EnrichmentResult:
    """Rebuild an ``EnrichmentResult`` from the legacy ``payload`` dict.

    The stored ``payload`` is the ``model_dump(mode="json")`` of an
    ``EnrichmentResult``; reconstruction goes through pydantic so the API
    response shape is identical to a fresh run.
    """
    return EnrichmentResult.model_validate(payload)


__all__ = [
    "FreshnessVerdict",
    "PersistedStats",
    "ProductRepository",
    "RunSummary",
    "SUCCESS_STATUSES",
    "build_enrichment_from_payload",
    "normalize_mpn",
    "record_reuse",
    "reuse_counter",
]


# ---------------------------------------------------------------------------
# Process-local counter for "we served a response from the database" hits.
# Module-level singleton; deliberately not persisted. The dashboard exposes
# this so operators can see the reuse path being taken without us inventing a
# new SQL table just for a request-level metric.
# ---------------------------------------------------------------------------


class _ReuseCounter:
    def __init__(self) -> None:
        self._value: int = 0

    def increment(self) -> int:
        self._value += 1
        return self._value

    @property
    def value(self) -> int:
        return self._value

    def reset(self) -> None:
        self._value = 0


reuse_counter = _ReuseCounter()


def record_reuse() -> int:
    """Increment the process-local reuse counter; returns the new value."""
    return reuse_counter.increment()
