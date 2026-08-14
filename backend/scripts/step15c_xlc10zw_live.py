"""TEMPORARY LIVE DIAGNOSTIC / REGRESSION UTILITY - NOT part of the application.

This script is a one-off diagnostic for STEP 15C (controlled MPN-only live
test for XLC10ZW). It is NOT part of the application pipeline: nothing under
`app/` imports or invokes it, it is not registered as an entrypoint or
service, and it must never be wired into production.

It is ENVIRONMENT-DEPENDENT:
- Requires live outbound network egress and TLS-verifiable certificates for the
  manufacturer domains it fetches (a sandbox behind a TLS-intercepting proxy
  may block retrieval with CERTIFICATE_VERIFY_FAILED - that is an environment
  limitation, not a code defect).
- Requires valid provider credentials in `.env` (DISCOVERY_PROVIDER=groq,
  LLM_PROVIDER=openrouter, etc.). Results are not guaranteed reproducible
  across environments or over time.

It runs the REAL, unmodified pipeline (EnrichmentService with zero overrides ->
settings-driven groq discovery + openrouter LLM + curated domain trust from
verified_brands.json, NO source_url, NO force flags, NO fakes, SourcePolicy
untouched), persists via the same repository calls the API route uses, then
prints the 9-section report.

Constraints honored by this script: it does NOT disable TLS verification, does
NOT add any SSL-verification bypass, and does NOT print any secrets/API keys.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from app.config import settings
from app.db.database import SessionLocal
from app.db.models import Job
from app.db.repository import ProductRepository
from app.identity.mapping import VerifiedBrandLookup
from app.pipeline.enrichment import EnrichmentRequest, EnrichmentService
from app.unihack.schema import DeliverySchema


MPN = "XLC10ZW"
REQUEST = EnrichmentRequest(
    Mfg_Part_Num=MPN,
    Part_Desc="XLC10ZW Makita 18V Cordless Vacuum (Bare)",
    E1_Brand="-- Unbranded --",
    Unilog_Brand="-- No Unilog Brand --",
    DIB_Brand="-- No DIB Brand --",
    Part_Manuf="Makita Usa Inc (5142)",
)


def main() -> int:
    lookup = VerifiedBrandLookup.default()

    # --- real pipeline, no overrides, no source_url -----------------------
    service = EnrichmentService()
    result = service.run(REQUEST)

    # --- persistence (mirrors POST /api/enrich) ---------------------------
    session = SessionLocal()
    record_id = None
    verdict = None
    source = "dataset"
    fresh = False
    try:
        job = Job(kind="enrich", status=result.processing.status.value, created_at=datetime.utcnow())
        session.add(job)
        session.flush()
        repo = ProductRepository()
        record = repo.save_enrichment(
            session,
            result,
            job_id=job.id,
            run_id=uuid.uuid4().hex,
            freshness_days=settings.product_cache_freshness_days,
        )
        session.commit()
        record_id = record.id
        rec, verdict = repo.find_fresh_by_mpn(session, MPN, settings.product_cache_freshness_days)
        if rec is not None and verdict is not None:
            if str(verdict) == "FRESH":
                source, fresh = "database", False
            elif str(verdict) == "STALE":
                source, fresh = "database", True
            else:
                source = "dataset"
    finally:
        session.close()

    print_report(result, lookup, record_id, verdict, source, fresh)
    return 0


def section(n: int, title: str) -> None:
    print(f"\n=== {n}. {title} ===")


def print_report(result, lookup, record_id, verdict, source, fresh) -> None:
    ident = result.discovery.product

    # 1. Resolved identity
    section(1, "Resolved identity")
    print(f"  manufacturer:        {ident.manufacturer or '(blank)'}")
    print(f"  brand:               {ident.brand or '(blank)'}")
    print(f"  verified_manufacturer: {ident.verified_manufacturer or '(blank)'}")
    print(f"  verified_brand:      {ident.verified_brand or '(blank)'}")
    print(f"  verified_trade_name: {ident.verified_trade_name or '(blank)'}")
    print(f"  provenance:          {ident.identity_provenance or '(none)'}")
    domains = lookup.domains_for(
        REQUEST.Mfg_Part_Num, REQUEST.E1_Brand, REQUEST.DIB_Brand, REQUEST.Part_Manuf,
    )
    print(f"  trusted domains:     {domains}")

    # 2. Discovery
    section(2, "Discovery")
    d = result.discovery
    print(f"  provider(s):         DISCOVERY_PROVIDER={settings.discovery_provider!r}")
    print(f"  total_discovered:    {d.total_discovered}")
    print(f"  allowed:             {len(d.candidates)}")
    prohibited = [c for c in d.rejected if "prohibit" in (c.rejection_reason or "").lower()]
    rejected = [c for c in d.rejected if c not in prohibited]
    print(f"  rejected:            {len(rejected)}")
    print(f"  prohibited:          {len(prohibited)}")
    print("  allowed URLs:")
    for c in d.candidates:
        print(f"    - {c.url}  (domain={getattr(c, 'domain', '?')})")
    print("  rejected/prohibited URLs (reason):")
    for c in d.rejected:
        print(f"    - {c.url}: {c.rejection_reason}")
    if d.provider_errors:
        print("  provider_errors:")
        for pe in d.provider_errors:
            print(f"    - {pe.provider_name}: {pe.error_kind}: {pe.message}")

    # 3. Retrieval
    section(3, "Retrieval")
    print(f"  evidence count:      {len(result.evidence)}")
    failures = []
    for e in result.evidence:
        size = len(e.text or "")
        status = e.retrieval_status
        print(f"    - {e.url}  status={status}  text_size={size}")
        if status not in ("success", "SUCCESS"):
            failures.append((e.url, e.error_kind, e.error_message))
    if failures:
        print("  failures:")
        for url, kind, msg in failures:
            print(f"    - {url}: {kind}: {msg}")
    else:
        print("  failures: none")

    # 4. Extraction
    section(4, "Extraction")
    ext = result.extraction
    attrs = ext.attributes if ext is not None else []
    print(f"  attribute count:     {len(attrs)}")
    for a in attrs:
        print(
            f"    - {getattr(a, 'name', '?')}: "
            f"raw={getattr(a, 'raw_value', '')!r} "
            f"norm={getattr(a, 'normalized_value', '')!r} "
            f"unit={getattr(a, 'unit', '')!r} "
            f"conf={getattr(a, 'confidence', '')} "
            f"evidence_ids={getattr(a, 'evidence_ids', [])}"
        )
    if ext is not None and getattr(ext, "rejected", None):
        print(f"  rejected attributes: {len(ext.rejected)}")
        for r in ext.rejected:
            print(f"    - {getattr(r, 'name', '?')}: {getattr(r, 'reason', '')}")

    # 5. Validation
    section(5, "Validation")
    v = result.validation
    if v is None:
        print("  (no validation summary produced)")
    else:
        counts = v.counts or {}
        print(f"  counts: {counts}")
        for va in v.attributes:
            print(
                f"    - {getattr(va, 'name', '?')}: "
                f"outcome={getattr(va, 'outcome', '?')}"
            )

    # 6. Descriptions
    section(6, "Descriptions")
    desc = result.product.descriptions if result.product is not None else None
    if desc is None:
        print("  (no descriptions produced)")
    else:
        fields = [
            "product_title", "short_description", "mobile_description",
            "invoice_description", "long_description", "retail_description",
            "marketing_description", "item_features", "with_", "application",
            "includes", "product_name",
        ]
        generated, nongen = [], []
        for f in fields:
            val = getattr(desc, f, "") or ""
            if isinstance(val, list):
                val = ", ".join(val)
            (generated if val.strip() else nongen).append(f)
        print(f"  generated variants:  {generated}")
        print(f"  non-generated:       {nongen}")
        if "invoice_description" in generated:
            print(f"  INVOICE_DESC value:  {getattr(desc, 'invoice_description', '')!r}")
        if "mobile_description" in generated:
            print(f"  MOBILE_DESC value:   {getattr(desc, 'mobile_description', '')!r}")
    print("  rule / grounding notes (from review_reasons):")
    for r in result.review_reasons:
        if any(k in r for k in ("INVOICE_DESC", "MOBILE_DESC", "description", "grounding")):
            print(f"    - {r}")
    # description stage status
    for s in result.stages:
        if str(getattr(s, "stage", "")).upper() == "DESCRIPTIONS":
            print(f"  descriptions stage status: {s.status} ({getattr(s, 'note', '')})")

    # 7. Persistence
    section(7, "Persistence")
    print(f"  database record ID:  {record_id}")
    if record_id is not None:
        print(f"  status:              {result.processing.status.value}")
        print(f"  freshness verdict:   {verdict}  (source={source}, stale={fresh})")
    else:
        print("  (persistence did not complete)")

    # 8. Delivery
    section(8, "Delivery")
    dv = result.delivery
    values = dv.values or []
    headers = dv.headers or []
    print(f"  column_count:        {dv.column_count}")
    populated = [i for i, v in enumerate(values) if (v or "").strip()]
    print(f"  populated field count: {len(populated)} of {len(values)}")
    try:
        schema = DeliverySchema.frozen()
    except Exception as exc:  # pragma: no cover
        schema = None
        print(f"  (could not load reference schema: {exc})")
    important = [
        "Mfg_Part_Num", "Part_Desc", "MANUFACTURER_NAME", "BRAND_NAME",
        "TRADE_NAME", "MOBILE_DESC", "INVOICE_DESC", "SHORT_DESC",
        "LONG_DESC", "PRODUCT_TITLE",
    ]
    print("  important populated fields:")
    pairs = dict(zip(headers, values)) if headers else {}
    for name in important:
        if schema is not None:
            idx = schema.index_of(name) if name in schema.headers else None
        else:
            idx = headers.index(name) if name in headers else None
        if idx is not None and idx < len(values):
            val = (values[idx] or "").strip()
            if val:
                print(f"    - {name}: {val[:120]}")
            else:
                print(f"    - {name}: (blank)")
        else:
            print(f"    - {name}: (column not found)")
    if dv.notes:
        print("  delivery notes:")
        for n in dv.notes:
            print(f"    - {n}")

    # 9. Overall
    section(9, "Overall")
    print(f"  processing status:   {result.processing.status.value}")
    print("  stage statuses:")
    for s in result.stages:
        print(f"    - {s.stage}: {s.status}  ({getattr(s, 'note', '')})")
    print(f"  review reasons ({len(result.review_reasons)}):")
    for r in result.review_reasons:
        print(f"    - {r}")


if __name__ == "__main__":
    raise SystemExit(main())
