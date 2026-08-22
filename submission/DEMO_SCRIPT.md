# Judge Demo Script (3–5 Minutes)

## Overview
Demonstrate the Product Truth Engine's end-to-end enrichment with two contrasting examples:
1. **Successful enrichment** – MPN `49-94-0013` (Milwaukee Tool Metal Cut Off Wheel) → verified identity from the trusted registry, high-confidence attributes, complete 252-column row.
2. **NEEDS_REVIEW safety case** – MPN `DCB518ASTS06G` (Diablo sanding belt, unverified manufacturer) → bootstrap cannot establish a trusted identity from retailer candidates, so MANUFACTURER_NAME/BRAND_NAME stay blank and the row is flagged for review — while still enriching every attribute the one trusted manufacturer page supports.

Both demos use the live deployment at https://unihack-product-truth-engine.onrender.com (or run locally: `uvicorn app.main:app --port 8000` + `npm run dev`).

---

## Setup (30 seconds)
1. Open the frontend URL in a browser.
2. Verify the page loads showing the input form and Quick Demo section.

---

## Demo 1: Successful Enrichment (2 minutes)

### Step 1: Input MPN (15 seconds)
- In the Quick Demo box, set MPN to `49-94-0013`.
- Leave all other fields blank (to show pure discovery).
- Click **Run Enrichment**.

### Step 2: Observe Pipeline Progress (30 seconds)
- Status transitions through stages: Identity → Discovery → Retrieval → Extraction → Validation → Description → Delivery.
- Review reasons show retailers being rejected in real time:
  - "rejected candidate https://greatlakespowertools.com/... : unknown external domain ... not manufacturer-owned"
  - "rejected candidate https://www.firstsupply.com/Product/MIL49940013 ..."
- Then: **"verified identity (mpn): manufacturer=Milwaukee Tool, brand=Milwaukee"** — resolved instantly from the verified-brand registry, no guessing.
- Completes well inside the 180-second cutoff (typically <15 s on free tier).

### Step 3: Examine Results (45 seconds)
- **Identity Section**:
  - MPN: `49-94-0013`
  - Manufacturer / Verified Manufacturer: `Milwaukee Tool`
  - Brand / Verified Brand: `Milwaukee`
  - Identity Provenance: `mpn` (from the trusted registry — not invented)
- **Attributes Table** (all confidence 1.00, each with evidence references):
  - Product Type: `Metal Cut Off Wheel`
  - Wheel Type: `1`
  - Diameter: `5` (in), Thickness: `0.045` (in), Arbor Size: `7/8` (in)
- **Descriptions** (evidence-grounded):
  - Product Title: `Milwaukee Tool 5 in. Metal Cut Off Wheel - Type 1`
  - Invoice Description: `MILWAUKEE 49-94-0013 5 IN. METAL CUT OFF` (auto-compacted to ≤40 chars)
- **Evidence Panel**:
  - Successful retrieval of the Milwaukee product page; failed fetches (Home Depot HTTP 403) are visible but never used as evidence.
  - Sibling products excluded: "extraction evidence excluded (sibling product 49-94-9000)..." — a different Milwaukee wheel could not contaminate this row.
- **Delivery CSV**: click **Download CSV** → exactly 252 columns; input passthrough intact, enriched columns filled, taxonomy columns blank (no official data — by design).

### Step 4: Highlight Trust & Safety (15 seconds)
- Every rejected retailer/distributor is listed with its rejection reason.
- The registry hit means zero hallucination risk for identity on this MPN.

---

## Demo 2: NEEDS_REVIEW Safety Case (2 minutes)

### Step 1: Input MPN (15 seconds)
- Change MPN to `DCB518ASTS06G`. Keep other fields blank. Click **Run Enrichment**.

### Step 2: Observe Pipeline Progress (30 seconds)
- Discovery finds mostly **retailer pages** for this Diablo sanding belt: Ace Hardware, Home Depot, McCoys, Amazon...
- Review reasons show the trust engine at work:
  - "identity bootstrap candidate domain 'acehardware.com' ... not trusted manufacturer source; not establishing authoritative identity"
  - "rejected candidate https://www.amazon.com/... : prohibited marketplace domain 'amazon.com'"
  - "verified identity: none found; MANUFACTURER_NAME/BRAND_NAME left blank (no trusted source)"
- Result status: **needs_review**.

### Step 3: Examine Results (45 seconds)
- **Identity Section**: Manufacturer / Brand / Verified fields all **blank**, provenance empty — the system refuses to assert an unverified manufacturer.
- **Attributes Table** (still enriched where evidence allowed):
  - Product Model: `DCB518ASTS06G`, Product Type: `Sanding Belts`, Brand: `Diablo`, Width: `1/2 in`, Length: `18 in` — all sourced from the ONE page that passed policy: `https://www.diablotools.com/products/DCB518ASTS06G` (the actual manufacturer).
- **Descriptions**: generated only from that trusted evidence ("Diablo DCB518ASTS06G 1/2 in x 18 in sanding belts…").
- **Delivery CSV**: still exactly 252 columns; `MANUFACTURER_NAME`, `TRADE_NAME`, Dept/Class/Fine remain blank rather than fabricated.

### Step 4: Explain NEEDS_REVIEW Value (15 seconds)
- NEEDS_REVIEW is a **feature**: the system says "I enriched everything I could prove, and here is exactly why I stopped short of asserting the manufacturer."
- A human can accept the partial row or research further — no wrong manufacturer ever enters procurement/inventory systems.

> **Note:** DuckDuckGo discovery is non-deterministic. On some runs bootstrap *does* find diablotools.com first and verifies the identity (provenance=bootstrap). Either outcome demonstrates the point: identity is only asserted when strong evidence exists.

---

## Closing (30 seconds)
- Summary line: evidence-backed, trust-aware enrichment that never fabricates, survives provider failures via LLM failover (Gemini → Groq → NVIDIA), and always emits the standards-compliant 252-column row.
- Invite judges to try their own MPN.

---

**End of Demo**.