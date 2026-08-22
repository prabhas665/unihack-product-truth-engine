# Architecture Overview

The Product Truth Engine processes a single product through the following stages:

## 1. Input
- Accepts the six official UniHack input columns: `Mfg_Part_Num`, `Part_Desc`, `E1_Brand`, `Unilog_Brand`, `DIB_Brand`, `Part_Manuf`
- At least one field must be non-blank; MPN is the primary key for enrichment.

## 2. Identity
- Looks up MPN/brand/manufacturer in `verified_brands.json` (trusted seed).
- If found, uses verified identity; otherwise triggers **identity bootstrap**.

## 3. Discovery
- Uses configured providers (DuckDuckGo primary, Groq-backed Gemini as fallback) to search for manufacturer product pages.
- Returns ranked candidates filtered by manufacturer-domain hints and SSRF safety.

## 4. Trusted Source Policy
- **Deny-by-default**: Candidates are rejected unless their hostname matches an allowed manufacturer domain.
- Allowed domains come from verified identity (bootstrap or seed) or explicit `manufacturer_domains` input.
- Blocks marketplaces (Amazon, eBay), retailers, and unknown external domains.
- Preserves candidates for traceability but does not use them for extraction if untrusted.

## 5. Retrieval
- Fetches HTML content from approved candidates (with SSRF guard, size/time limits, TLS verification).
- Converts to plain text; tracks success/failure per candidate.

## 6. Evidence Selection
- From successful retrievals, selects evidence that:
  - Contains the exact MPN (case-insensitive) in text or URL path.
  - Passes sibling contamination check (no mention of different MPNs from same manufacturer).
  - Is not from a marketplace or distributor page.
- Each selected piece becomes an `EvidenceRecord` with a stable ID.

## 7. AI Extraction
- Uses LLM (with failover chain) to extract attributes from evidence snippets.
- Prompt engineering enforces evidence citation: every extracted value must reference the evidence ID(s) that support it.
- Extraction fails if no usable evidence; never hallucinates values.

## 8. Claim Support Validation
- Normalizes extracted values (UOM, LOV) where official resources are available.
- Conflicts between candidates are recorded; resolution logic flags for review.
- Validation coverage reflects how many attributes could be normalized/validated.

## 9. Description Generation
- LLM generates standardized descriptions (product_title, short_description, etc.) strictly from evidence.
- Falls back to rule-based templates if LLM unavailable; never invents specifics.

## 10. 252-column Delivery
- Maps internal product intelligence to the official UniHack delivery schema.
- Passthrough input columns remain unchanged; enriched attributes fill appropriate columns.
- Missing official taxonomy/LOV/UOM data leaves fields blank rather than fabricating.
- Output is a CSV-ready 252-column row with full provenance traceability.

## Data Flow Example
Input MPN: `49-94-0013` (no other fields)
→ Identity: Registry hit in verified_brands.json (by_mpn) → verified identity: manufacturer=Milwaukee Tool, brand=Milwaukee, provenance=mpn
→ Trusted Policy: manufacturer domains = milwaukeetool.com (+ patterns) — deny-by-default for everything else
→ Discovery: DuckDuckGo finds candidate pages; retailers (greatlakespowertools.com, firstsupply.com, homedepot.com...) rejected by policy with recorded reasons
→ Retrieval: Approved manufacturer page fetched (SSRF-guarded, TLS-verified); Home Depot 403s are recorded but never used
→ Evidence Selection: MPN present in page; sibling products (e.g., 49-94-9000) excluded from extraction evidence
→ Extraction: Attributes extracted with evidence citations — Product Type=Metal Cut Off Wheel, Diameter=5 in, Thickness=0.045 in, Arbor=7/8 in (all confidence 1.00)
→ Validation: UOM normalization applied; invoice description compacted to ≤40 chars (`MILWAUKEE 49-94-0013 5 IN. METAL CUT OFF`)
→ Descriptions: LLM writes product_title / short / mobile / invoice / long / retail / marketing strictly from evidence
→ Delivery: exactly 252 columns — input passthrough intact, enriched attributes filled, unavailable taxonomy left blank

### Contrast case: `DCB518ASTS06G`
Discovery returns mostly retailer/marketplace pages (Ace Hardware, Amazon, McCoys). Bootstrap cannot verify the manufacturer from a trusted domain, so verified identity stays empty and MANUFACTURER_NAME/BRAND_NAME are left blank → result is flagged **needs_review** while all attributes supported by the one trusted page (diablotools.com) are still enriched. No fabrication at any point.