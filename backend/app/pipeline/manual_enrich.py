"""Manual single-product enrichment command (Step 6D CLI).

Runs the REAL pipeline end-to-end for one product - discovery providers from
settings (or ONE operator-confirmed URL via --url), real HTML/PDF retrieval,
the configured LLM provider, validation, and the 252-column delivery mapping -
then writes the delivery row to a generated CSV (never to the official
reference file).

Controlled live test example (operator-confirmed manufacturer URL):

    python -m app.pipeline.manual_enrich ^
        --Mfg_Part_Num "XLC10ZW" ^
        "--Part_Desc=XLC10ZW Makita 18V Cordless Vacuum (Bare)" ^
        "--E1_Brand=-- Unbranded --" ^
        "--Unilog_Brand=-- No Unilog Brand --" ^
        "--DIB_Brand=-- No DIB Brand --" ^
        "--Part_Manuf=Makita Usa Inc (5142)" ^
        --url "https://makitatools.com/products/details/XLC10ZW" ^
        --manufacturer-domain "makitatools.com"

`--url` requires at least one `--manufacturer-domain` so the SourcePolicy can
judge the candidate (manufacturer-owned -> ALLOWED, otherwise -> REJECTED);
the URL itself is never trusted.

If the target server sends an incomplete TLS chain (site works in browsers,
fails in Python/OpenSSL), pass the missing intermediate/CA bundle via
`--ca-cert <file.pem>` (repeatable): the transport still verifies everything,
it just also trusts the listed certificates.

Secrets (LLM_API_KEY, SEARCH_PROVIDER_API_KEY) come from the environment /
.env and are never printed. Run with any network/API settings you have
configured; every failure is reported as a reviewable result, not a crash.
"""

from __future__ import annotations

import argparse
import re
import ssl
import sys
from pathlib import Path

import httpx

from app.core.domain import SourceType
from app.pipeline.enrichment import (
    EnrichmentRequest,
    EnrichmentService,
    REQUEST_FIELDS,
    StageStatus,
)
from app.sources.providers.manual_url import ManualUrlProvider
from app.unihack.paths import repo_root


def _safe_filename(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("._")
    return cleaned or "unknown"


def _build_verify_transport(ca_certs: list[str]) -> httpx.BaseTransport | None:
    """A transport that still verifies TLS but also trusts extra CA/intermediate
    bundles (for servers that send an incomplete chain - e.g. no intermediate).

    Verification is never disabled: the extra certificates are added to the
    default verified context, so the leaf must still chain to a trusted root.
    """
    if not ca_certs:
        return None
    context = ssl.create_default_context()
    for path in ca_certs:
        context.load_verify_locations(cafile=path)
    return httpx.HTTPTransport(verify=context)


def _parse_source_type(value: str) -> SourceType:
    """Map a --source-type argument onto the SourceType enum (lenient)."""
    text = (value or "").strip().lower()
    for source_type in SourceType:
        if source_type.value == text or source_type.name.lower() == text:
            return source_type
    raise ValueError(
        f"unknown source type {value!r}; use one of "
        + ", ".join(st.value for st in SourceType)
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m app.pipeline.manual_enrich",
        description="Run the real enrichment pipeline for ONE product and "
        "write the 252-column delivery row to a CSV.",
    )
    for field in REQUEST_FIELDS:
        parser.add_argument(f"--{field}", default="", help=f"official input field {field}")
    parser.add_argument(
        "--url",
        default="",
        help="operator-confirmed source URL (used instead of discovery "
        "providers); requires --manufacturer-domain",
    )
    parser.add_argument(
        "--source-type",
        default=SourceType.MANUFACTURER_PRODUCT_PAGE.value,
        help="source type for the --url candidate (default: "
        "manufacturer_product_page)",
    )
    parser.add_argument(
        "--manufacturer-domain",
        action="append",
        default=[],
        help="manufacturer-owned domain for the source policy (repeatable)",
    )
    parser.add_argument(
        "--ca-cert",
        action="append",
        default=[],
        help="extra CA/intermediate PEM file trusted by the verified retrieval "
        "transport (repeatable; TLS verification is never disabled)",
    )
    parser.add_argument(
        "--output",
        default="",
        help="output CSV path (default: data/delivery/<MPN>.csv)",
    )
    args = parser.parse_args(argv)

    if args.url and not args.manufacturer_domain:
        parser.error(
            "--url requires at least one --manufacturer-domain so the "
            "SourcePolicy can evaluate the candidate"
        )

    request = EnrichmentRequest(
        **{field: getattr(args, field) for field in REQUEST_FIELDS}
    )
    output = (
        args.output
        or str(settings.runtime_data_dir() / "delivery" / f"{_safe_filename(request.Mfg_Part_Num)}.csv")
    )

    providers = None
    if args.url:
        try:
            source_type = _parse_source_type(args.source_type)
        except ValueError as exc:
            parser.error(str(exc))
        providers = [
            ManualUrlProvider(
                args.url,
                source_type=source_type,
                title=request.Part_Desc,
            )
        ]

    service = EnrichmentService(
        providers=providers,
        manufacturer_domains=args.manufacturer_domain,
        transport=_build_verify_transport(args.ca_cert),
    )
    result = service.run(request, output_path=output)

    print("=== UniHack single-product enrichment ===")
    print(f"processing: {result.processing.status.value}")
    for state in result.stages:
        print(f"  {state.stage.value:<20} {state.status.value:<10} {state.note}")
    print(f"discovery: {result.discovery.total_discovered} discovered, "
          f"{len(result.discovery.candidates)} allowed, "
          f"{len(result.discovery.rejected)} rejected, "
          f"{len(result.discovery.provider_errors)} provider error(s)")
    for record in result.evidence:
        final = f" (final: {record.final_url})" if record.final_url else ""
        print(f"  evidence [{record.evidence_id}] [{record.retrieval_status.value}] {record.url}{final}")
    if result.extraction is not None:
        print(f"extraction: {len(result.extraction.attributes)} accepted, "
              f"{len(result.extraction.rejected)} rejected")
        for attribute in result.extraction.attributes:
            print(
                f"    {attribute.name:<32} raw={attribute.raw_value!r:<28} "
                f"normalized={attribute.normalized_value!r:<28} "
                f"unit={attribute.unit!r:<10} conf={attribute.confidence:<5.2f} "
                f"evidence={attribute.evidence_ids}"
            )
        for rejected in result.extraction.rejected:
            print(f"    rejected {rejected.name!r}: {rejected.reason}")
    if result.validation is not None:
        print(f"validation counts: {result.validation.counts}")
        for attribute in result.validation.attributes:
            print(
                f"    {attribute.name:<32} outcome={attribute.outcome.value:<13} "
                f"conf={attribute.confidence:<5.2f} evidence={attribute.evidence_refs}"
            )
    print(f"delivery: {result.delivery.column_count} columns -> {output}")
    for reason in result.review_reasons:
        print(f"  review: {reason}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
