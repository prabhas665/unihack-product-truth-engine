"""GET /api/lookup: find products in the persistent store (Step 10B).

Lookup order:

1. Normalize the query MPN (strip whitespace, lower-case).
2. Ask ``ProductRepository`` for FRESH stored records. When at least one
   exists, rebuild the stored ``EnrichmentResult`` and return it together
   with a ``source = "database"`` flag. NO LLM call and NO retrieval happen
   on this path.
3. Otherwise surface any stored record (fresh OR stale) with a ``stale``
   flag so the UI can warn that a prior enrichment exists but is not fresh.

The lookup never reads the official UniHack input CSV; only the persistent
store is consulted.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.config import settings
from app.db.database import get_session
from app.db.repository import (
    FreshnessVerdict,
    ProductRepository,
    record_reuse,
)


class StoredRecordView(BaseModel):
    """One stored product record surfaced by the lookup."""

    record_id: int
    part_number: str
    manufacturer: str = ""
    brand: str = ""
    description: str = ""
    status: str = ""
    last_enriched_at: str | None = None
    source_freshness_days: int = 0


class LookupResult(BaseModel):
    query: str
    total_matches: int
    source: str = "none"  # "database" | "none"
    stale: bool = False
    rows: list = Field(default_factory=list)
    stored_records: list[StoredRecordView] = Field(default_factory=list)


router = APIRouter(prefix="/api", tags=["lookup"])


def _to_stored_view(record) -> StoredRecordView:
    return StoredRecordView(
        record_id=record.id,
        part_number=record.part_number or "",
        manufacturer=record.manufacturer or "",
        brand=record.brand or "",
        description=record.description or "",
        status=record.status or "",
        last_enriched_at=record.last_enriched_at.isoformat()
        if record.last_enriched_at
        else None,
        source_freshness_days=record.source_freshness_days or 0,
    )


@router.get("/lookup", response_model=LookupResult)
def lookup(
    mpn: str = Query(..., min_length=1, description="Manufacturer part number"),
    session: Session = Depends(get_session),
) -> LookupResult:
    repo = ProductRepository()

    # 1. Persistent store first -- the fast path that skips retrieval and LLM.
    fresh_records = repo.find_fresh_records_by_mpn(
        session, mpn, settings.product_cache_freshness_days
    )
    if fresh_records:
        record_reuse()
        return LookupResult(
            query=mpn,
            total_matches=len(fresh_records),
            source="database",
            stale=False,
            rows=[],
            stored_records=[_to_stored_view(record) for record in fresh_records],
        )

    # 2. Any stored record exists but is stale (or otherwise unusable as a
    #    fresh hit). Surface it with a stale flag so the UI can warn.
    any_records = repo.find_by_mpn(session, mpn)
    if any_records:
        return LookupResult(
            query=mpn,
            total_matches=len(any_records),
            source="database",
            stale=True,
            rows=[],
            stored_records=[_to_stored_view(record) for record in any_records],
        )

    return LookupResult(
        query=mpn,
        total_matches=0,
        source="none",
        stale=False,
        rows=[],
        stored_records=[],
    )


# Re-export ``FreshnessVerdict`` so tests can introspect the verdict mapping
# without importing the repository directly.
__all__ = ["FreshnessVerdict", "LookupResult", "StoredRecordView"]
