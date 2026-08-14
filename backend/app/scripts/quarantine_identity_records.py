"""Audit or quarantine persisted rows that violate the P0 MPN contract.

Dry-run by default. Pass ``--apply`` only after reviewing the printed record
ids; affected records are marked ``quarantined_identity`` (never deleted), so
the cache's SUCCESS_STATUSES filter can no longer reuse them.
"""

from __future__ import annotations

import argparse
import json

from app.db.database import SessionLocal, init_db
from app.db.models import ProductRecordModel
from app.db.repository import (
    SUCCESS_STATUSES,
    build_enrichment_from_payload,
    normalize_mpn,
)
from app.pipeline.enrichment import enrichment_identity_errors

DEMO_MPN = "XLC10ZW"
DEMO_DESCRIPTION = "XLC10ZW Makita 18V Cordless Vacuum (Bare)"
DEMO_SOURCE_URL = "https://makitatools.com/products/details/XLC10ZW"
QUARANTINE_STATUS = "quarantined_identity"


def record_errors(record: ProductRecordModel) -> list[str]:
    """Return concrete cache-integrity or known Quick Demo contamination errors."""
    errors: list[str] = []
    target = normalize_mpn(record.part_number)
    if record.part_number != target:
        errors.append("stored part_number is not canonical")

    try:
        payload = json.loads(record.payload or "{}")
        rebuilt = build_enrichment_from_payload(payload)
    except (TypeError, ValueError):
        return errors + ["payload cannot be rebuilt as EnrichmentResult"]

    errors.extend(enrichment_identity_errors(rebuilt, target))
    request = payload.get("request", {}) if isinstance(payload, dict) else {}
    if target != DEMO_MPN and (
        request.get("source_url") == DEMO_SOURCE_URL
        or request.get("Part_Desc") == DEMO_DESCRIPTION
    ):
        errors.append("non-demo MPN contains the XLC10ZW Quick Demo source or description")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="mark listed successful records quarantined_identity; default is read-only",
    )
    args = parser.parse_args()

    init_db()
    session = SessionLocal()
    try:
        records = (
            session.query(ProductRecordModel)
            .filter(ProductRecordModel.status.in_(list(SUCCESS_STATUSES)))
            .order_by(ProductRecordModel.id)
            .all()
        )
        affected = 0
        for record in records:
            errors = record_errors(record)
            if not errors:
                continue
            affected += 1
            print(f"record={record.id} mpn={record.part_number!r}: {'; '.join(errors)}")
            if args.apply:
                record.status = QUARANTINE_STATUS
        if args.apply:
            session.commit()
            print(f"quarantined {affected} record(s)")
        else:
            print(f"dry run: {affected} record(s) would be quarantined")
        return 0
    finally:
        session.close()


if __name__ == "__main__":
    raise SystemExit(main())
