"""STEP 11A — Controlled live Gemini discovery test (observational only).

Runs ONE live Gemini grounding call for a single known product and reports
what Gemini independently discovers, then passes the candidates through the
EXISTING SourcePolicy. No enrichment, no OpenRouter, no page retrieval, no
file writes — observation only.

Run from the backend/ directory so pydantic_settings picks up backend/.env:

    cd D:\\unihack\\backend
    python tests/test_gemini_live_smoke.py
"""

from __future__ import annotations

from app.config import settings
from app.core.domain import ProductIdentity
from app.sources.candidates import CandidateStatus, ManufacturerRelationship
from app.sources.discovery import DiscoveryContext, run_discovery
from app.sources.providers.gemini_search import GeminiSearchProvider

# --- ground truth (comparison only; NOT injected into the provider) ---
KNOWN_URL = "https://makitatools.com/products/details/XLC10ZW"
KNOWN_DOMAIN = "makitatools.com"


def main() -> None:
    # Safety: bail loudly if no key is configured.
    if not getattr(settings, "GEMINI_API_KEY", ""):
        raise SystemExit("GEMINI_API_KEY not set in backend/.env — refusing live call")

    product = ProductIdentity(
        manufacturer="Makita Usa Inc",
        brand="Makita",
        mpn="XLC10ZW",
        raw_description="XLC10ZW Makita 18V Cordless Vacuum (Bare)",
    )
    context = DiscoveryContext(
        product=product,
        manufacturer_domains=[KNOWN_DOMAIN],
    )

    print("=" * 70)
    print("STEP 11A — LIVE GEMINI DISCOVERY SMOKE TEST")
    print("=" * 70)
    print(f"Product : {product.manufacturer} | MPN={product.mpn}")
    print(f"Model   : {getattr(settings, 'GEMINI_MODEL', '<default>')}")
    print(f"Key present: {'YES' if getattr(settings, 'GEMINI_API_KEY', '') else 'NO'}")
    print("-" * 70)

    # Build the live provider from .env (uses GEMINI_API_KEY etc.)
    provider = GeminiSearchProvider.from_settings(settings)

    # Single live grounding call, then through the existing SourcePolicy.
    result = run_discovery(
        product=product,
        providers=[provider],
        context=context,
    )

    discovered = result.candidates + result.rejected

    # ---- Provider errors (failures must be visible, never silent) --------
    print("\n[0] PROVIDER ERRORS")
    if result.provider_errors:
        for e in result.provider_errors:
            print(f"    - name={e.provider_name} kind={e.error_kind}")
            print(f"      {e.message}")
    else:
        print("    none")

    # ---- A. Raw Gemini grounding response --------------------------------
    print("\n[A] RAW GEMINI GROUNDING RESPONSE")
    print(f"    groundingChunks returned : {result.total_discovered}")
    for i, c in enumerate(discovered, 1):
        print(f"    {i:>2}. {c.title!r}")
        print(f"        {c.url}")

    # ---- B. Candidate summary --------------------------------------------
    print("\n[B] CANDIDATE SUMMARY")
    print(f"    total candidates : {len(discovered)}")
    for c in discovered:
        print(f"    - url={c.url}")
        print(f"      domain={c.domain}  source_type={c.source_type.value}")

    # ---- C. Policy decisions ---------------------------------------------
    print("\n[C] POLICY DECISIONS (ALLOWED vs REJECTED)")
    allowed = [c for c in discovered if c.status == CandidateStatus.ALLOWED]
    rejected = [c for c in discovered if c.status != CandidateStatus.ALLOWED]
    print(f"    ALLOWED  : {len(allowed)}")
    print(f"    REJECTED : {len(rejected)}")
    for c in rejected:
        print(f"    - {c.url}")
        print(f"      status={c.status.value} reason={c.rejection_reason!r}")

    # ---- D. Ground-truth check -------------------------------------------
    print("\n[D] GROUND-TRUTH CHECK")
    discovered_urls = [c.url for c in discovered]
    exact = KNOWN_URL in discovered_urls
    same_path_others = [
        u for u in discovered_urls
        if KNOWN_DOMAIN in u and "xlc10zw" in u.lower()
    ]
    print(f"    known URL exactly discovered : {exact}")
    print(f"    makita XLC10ZW variants found : {same_path_others or 'NONE'}")
    for c in discovered:
        if KNOWN_DOMAIN in c.url:
            print(f"    -> makita-owned candidate: {c.url}")
            print(f"       relationship={c.manufacturer_relationship.value} "
                  f"status={c.status.value}")

    # ---- E. Fabrication check --------------------------------------------
    print("\n[E] FABRICATION CHECK")
    bad = [
        c.url for c in discovered
        if not (str(c.url).startswith("http://") or str(c.url).startswith("https://"))
    ]
    print(f"    non-http(s) URLs : {bad or 'NONE'}")
    print(f"    result           : {'PASS (no fabricated URLs)' if not bad else 'FAIL'}")

    # ---- F. API key safety -----------------------------------------------
    print("\n[F] API KEY SAFETY")
    printed_key = False
    for c in discovered:
        if getattr(settings, "GEMINI_API_KEY", "") and getattr(settings, "GEMINI_API_KEY", "") in str(c.url):
            printed_key = True
    print(f"    GEMINI_API_KEY printed in output : {'YES (BAD)' if printed_key else 'NO (good)'}")

    print("\n" + "=" * 70)
    print("END OF LIVE TEST — this was a single live Gemini grounding call.")
    print("=" * 70)


if __name__ == "__main__":
    main()
