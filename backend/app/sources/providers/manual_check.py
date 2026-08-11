"""Manual integration check for the real search provider (Step 6B).

NOT a test and NOT run by the test suite: this command calls the REAL search
API configured in the backend environment and runs the full discovery flow
(provider -> SourcePolicy -> deterministic ranking) on one product, printing
the query, every candidate's policy outcome, and the final ranked list.

Prerequisites (backend .env or environment variables):
  DISCOVERY_PROVIDER=search
  SEARCH_PROVIDER_API_KEY=<your key>
  (optional) SEARCH_PROVIDER_BASE_URL=<custom endpoint>
  (optional) SOURCE_ALLOWED_DOMAINS=<trusted external domains>

Usage (from the repo root):
  .\.venv\Scripts\python.exe -m app.sources.providers.manual_check ^
      --mpn DCB518ASTS06G --manufacturer "Freud Inc" ^
      --description "1/2 inch x 18 inch Sanding Belt 6 pack" ^
      --manufacturer-domains "freudtools.com"
"""

from __future__ import annotations

import argparse

from app.core.domain import ProductIdentity
from app.sources.discovery import DiscoveryContext, run_discovery


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run real search discovery on one product."
    )
    parser.add_argument("--mpn", default="", help="manufacturer part number")
    parser.add_argument("--manufacturer", default="", help="manufacturer name")
    parser.add_argument("--brand", default="", help="brand name")
    parser.add_argument(
        "--description", default="", help="raw product description"
    )
    parser.add_argument(
        "--manufacturer-domains",
        default="",
        help="comma-separated owned domains (used by the source policy)",
    )
    return parser


def main() -> int:
    args = build_arg_parser().parse_args()
    product = ProductIdentity(
        manufacturer=args.manufacturer,
        brand=args.brand,
        mpn=args.mpn,
        raw_description=args.description,
    )
    manufacturer_domains = [
        item.strip().lower()
        for item in args.manufacturer_domains.split(",")
        if item.strip()
    ]
    context = DiscoveryContext(
        product=product, manufacturer_domains=manufacturer_domains
    )
    result = run_discovery(product, context=context)

    print(f"product       : {product.manufacturer} {product.brand} {product.mpn}")
    print(f"discovered    : {result.total_discovered}")
    for error in result.provider_errors:
        print(f"provider error: [{error.error_kind}] {error.provider_name}: {error.message}")
    print("\n-- allowed candidates (ranked best-first) --")
    for candidate in result.candidates:
        print(
            f"  {candidate.relevance_score:.2f} {candidate.domain:<30} "
            f"{candidate.source_type.value:<30} {candidate.url}"
        )
    print("\n-- rejected / prohibited candidates --")
    for candidate in result.rejected:
        print(
            f"  [{candidate.status.value}] {candidate.domain:<30} "
            f"{candidate.rejection_reason}"
        )
    if not result.candidates:
        print("\nNo allowed candidates. Check the policy reason above; search "
              "results are never trusted automatically.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
