"""POST /api/enrich: single-product enrichment (Step 6D / Step 8B / Step 10B).

Accepts the six official UniHack input fields plus an OPTIONAL
operator-confirmed manufacturer source URL as JSON and returns the full,
reviewable EnrichmentResult.

When ``source_url`` is supplied, a ManualUrlProvider is injected for THIS
request only (never registered globally): the URL becomes a PENDING
SourceCandidate whose hostname is used only as this request's
manufacturer-domain candidate, so the SourcePolicy still decides
ALLOWED/REJECTED (marketplace rejection runs first) and retrieval still
refuses anything that is not ALLOWED. Without a URL, discovery behaves
exactly as before. The service is built per request from settings (real
retrieval, real LLM provider when configured); tests override the service
via dependency_overrides to stay fully offline.

Step 10B changes:
* Optional query parameter ``retrieve_from_db=true`` (default false). When
  true and a FRESH stored record exists for the request's MPN, the stored
  EnrichmentResult is rebuilt and returned with a ``source`` meta block
  instead of running the pipeline. When false, the existing pipeline is
  always invoked (no behavior change for existing callers).
* After a successful pipeline run, the result is persisted via
  ``ProductRepository``. A failure during persistence surfaces a
  sanitized 500; DB internals / secrets are never exposed.
"""

from __future__ import annotations

import json
import re
import traceback
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api.routes.lookup import LookupResult, StoredRecordView
from app.config import settings
from app.db.database import get_session
from app.db.models import Job
from app.db.repository import (
    FreshnessVerdict,
    ProductRepository,
    build_enrichment_from_payload,
    normalize_mpn,
)
from app.identity.mapping import is_placeholder
from app.pipeline.enrichment import (
    EnrichmentRequest,
    EnrichmentResult,
    EnrichmentService,
    IdentityInvariantError,
    require_enrichment_identity,
)
from app.sources.candidates import normalize_domain
from app.sources.discovery import SourceProvider
from app.sources.providers.manual_url import ManualUrlProvider

router = APIRouter(prefix="/api", tags=["enrich"])

# Corporate name noise filtered out when comparing manufacturer contexts.
_MANUFACTURER_STOP_WORDS = {
    "AND",
    "CO",
    "COMPANY",
    "COMPANIES",
    "CORP",
    "CORPORATION",
    "DIVISION",
    "ELECTRIC",
    "GROUP",
    "INC",
    "INDUSTRIAL",
    "INDUSTRIES",
    "LTD",
    "LLC",
    "MACHINE",
    "MACHINES",
    "MACHINERY",
    "OF",
    "PRODUCTS",
    "SERVICES",
    "THE",
    "TOOL",
    "TOOLS",
    "USA",
    "WORKS",
}


def _manufacturer_tokens(value: str) -> set[str]:
    """Uppercased, de-noised manufacturer name tokens from a string.

    Parenthetical codes are stripped ("Freud Inc (2435)" -> FREUD, INC) and
    generic corporate words removed, so "Makita Usa Inc" and "Makita"
    compare equal. Pure-numeric tokens (manufacturer codes) are dropped.
    """
    raw = set(re.findall(r"[A-Z0-9]+", (value or "").upper()))
    return {
        token
        for token in raw
        if len(token) >= 2
        and not token.isdigit()
        and token not in _MANUFACTURER_STOP_WORDS
    }


def _manufacturers_compatible(
    requested_part_manuf: str, stored_manufacturer: str
) -> bool:
    """True when the request's Part_Manuf matches the stored manufacturer.

    Conservative cache isolation: a stored record is served only when the
    request does not clearly belong to a DIFFERENT manufacturer. Blank,
    placeholder, or unknown names on either side cannot prove a mismatch, so
    they never reject (legacy records without a manufacturer stay servable).
    """
    if is_placeholder(requested_part_manuf) or is_placeholder(stored_manufacturer):
        return True
    requested_tokens = _manufacturer_tokens(requested_part_manuf)
    stored_tokens = _manufacturer_tokens(stored_manufacturer)
    if not requested_tokens or not stored_tokens:
        return True
    return bool(requested_tokens & stored_tokens)


def build_manual_source(
    source_url: str, title: str = ""
) -> tuple[list[SourceProvider] | None, list[str] | None]:
    """Request-scoped manual source override for an optional source URL.

    Returns (providers, manufacturer_domains) to pass into an
    EnrichmentService: (None, None) when no URL is given (discovery stays
    settings-driven), or a ManualUrlProvider plus the URL's own hostname as
    the only manufacturer-domain candidate. The hostname is derived safely
    via normalize_domain and is never trusted globally; an unparseable
    URL yields no manufacturer domain, so the policy rejects it like any
    unknown external domain.
    """
    url = (source_url or "").strip()
    if not url:
        return None, None
    domain = normalize_domain(url)
    domains = [domain] if domain else []
    return [ManualUrlProvider(url, title=title)], domains


def get_enrichment_service(request: EnrichmentRequest) -> EnrichmentService:
    """Build the pipeline service for one request, from settings + source_url."""
    providers, manufacturer_domains = build_manual_source(
        request.source_url, request.Part_Desc
    )
    return EnrichmentService(
        providers=providers,
        manufacturer_domains=manufacturer_domains,
    )


def _record_to_lookup_view(record) -> StoredRecordView:
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


@router.post("/enrich", response_model=EnrichmentResult)
def enrich(
    request: EnrichmentRequest,
    retrieve_from_db: bool = Query(
        False,
        description=(
            "When true and a fresh stored product exists for this MPN, "
            "return the stored enrichment without running the pipeline."
        ),
    ),
    service: EnrichmentService = Depends(get_enrichment_service),
    session: Session = Depends(get_session),
) -> EnrichmentResult:
    repo = ProductRepository()
    mpn = request.Mfg_Part_Num

    # Step 10B: optional DB-first path. Only ever honors a FRESH hit.
    if retrieve_from_db and mpn:
        record, verdict = repo.find_fresh_by_mpn(
            session, mpn, settings.product_cache_freshness_days
        )
        if verdict == FreshnessVerdict.FRESH and record is not None:
            # The stored ``payload`` is the full ``EnrichmentResult.model_dump``;
            # rebuild through pydantic so the response shape is identical to
            # a fresh run. A row/payload mismatch is cache corruption: fail
            # closed and run the requested product through the pipeline.
            try:
                payload = json.loads(record.payload or "{}")
                rebuilt = build_enrichment_from_payload(payload)
                if record.part_number != mpn or normalize_mpn(record.part_number) != mpn:
                    rebuilt = None
                elif rebuilt is not None:
                    require_enrichment_identity(rebuilt, mpn)
                    # Cache isolation: a fresh MPN record may belong to a
                    # DIFFERENT manufacturer (MPNs are not globally unique).
                    # When the request clearly names another manufacturer,
                    # the stored result is rejected and the pipeline runs.
                    stored_manufacturer = (
                        rebuilt.product.identity.manufacturer
                        if rebuilt.product is not None
                        else ""
                    )
                    if not _manufacturers_compatible(
                        request.Part_Manuf, stored_manufacturer
                    ):
                        rebuilt = None
            except (ValueError, TypeError, IdentityInvariantError):
                rebuilt = None
            if rebuilt is not None:
                # Attach a small meta block so the UI can show "Loaded from
                # Product Intelligence Store". The pydantic model would
                # reject unknown fields, so we smuggle this through a custom
                # header instead of the body for the API; the frontend reads
                # ``X-Source`` to drive the banner.
                from starlette.responses import JSONResponse  # noqa: WPS433

                body = rebuilt.model_dump(mode="json")
                body["__source__"] = "database"
                body["__stale__"] = False
                body["__record_id__"] = record.id
                body["__last_enriched_at__"] = (
                    record.last_enriched_at.isoformat()
                    if record.last_enriched_at
                    else None
                )
                return JSONResponse(
                    content=body,
                    media_type="application/json",
                    headers={
                        "X-Source": "database",
                        "X-Stale": "false",
                        "X-Record-Id": str(record.id),
                    },
                )

    # Default path: run the real pipeline.
    try:
        result = service.run(request)
        require_enrichment_identity(result, mpn)
    except IdentityInvariantError as exc:
        raise HTTPException(
            status_code=500,
            detail=(
                "enrichment produced an inconsistent product identity; "
                "the result was not returned or stored."
            ),
            headers={"X-Identity-Error": "true"},
        ) from exc

    # Step 10B: persist the result. A failure here becomes a sanitized 500
    # so the operator knows the pipeline succeeded but the database write
    # did not; DB internals / secrets / exception args are never returned.
    try:
        job = Job(
            kind="enrich",
            status=result.processing.status.value,
            created_at=__import__("datetime").datetime.utcnow(),
        )
        session.add(job)
        session.flush()
        repo.save_enrichment(
            session,
            result,
            job_id=job.id,
            run_id=uuid.uuid4().hex,
            freshness_days=settings.product_cache_freshness_days,
        )
        session.commit()
    except Exception:
        session.rollback()
        trace = traceback.format_exc(limit=1)
        raise HTTPException(
            status_code=500,
            detail=(
                "enrichment pipeline produced a result but persistence "
                "failed; the run was not stored. Check the server logs."
            ),
            headers={"X-Source": "fresh", "X-Persistence-Error": "true"},
        ) from None

    return result


__all__ = [
    "LookupResult",
    "build_manual_source",
    "enrich",
    "get_enrichment_service",
]
