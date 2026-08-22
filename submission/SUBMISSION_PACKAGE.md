# UniHack Submission Package — Product Truth Engine

---

## 1. Final Project Description

**Problem (2–3 sentences).**
Industrial distributors receive product data as little more than a manufacturer part number, yet procurement, e-commerce, and compliance systems need hundreds of populated fields. Asking a generic LLM to "fill in the specs" produces confident hallucinations — wrong voltages, invented certifications, misattributed manufacturers — that are unacceptable when a wrong value can stop a production line.

**Solution.**
The Product Truth Engine is an evidence-first enrichment pipeline: it resolves the product's identity against a verified manufacturer registry, discovers only manufacturer-owned web sources through a deny-by-default trust policy, retrieves and screens page evidence (rejecting retailers, marketplaces, and sibling products), extracts attributes with per-value evidence citations via a multi-provider LLM chain, validates and normalizes them, generates commerce-ready descriptions grounded strictly in that evidence, and emits the official UniHack 252-column delivery row — with every gap left honestly blank and flagged NEEDS_REVIEW instead of fabricated.

**Key differentiators.**
- Evidence-first: no attribute without a retrievable source; claim-support gate rejects values the snippet doesn't contain.
- Trust-aware identity: deny-by-default SourcePolicy; bootstrap requires exact-MPN + manufacturer-consistency + sibling-check on a non-marketplace domain.
- Honest uncertainty: NEEDS_REVIEW with machine-readable reasons is a first-class outcome.
- Resilience: Gemini (5-key rotation) → Groq → NVIDIA failover; DuckDuckGo discovery free-tier operable end-to-end.

**Technologies used.**
FastAPI (Python 3.11), Pydantic v2, SQLAlchemy + SQLite (persistent disk on Render), React 18 + TypeScript + Vite, Vitest, Gemini API (primary LLM), Groq-hosted allam-2-7b (fallback), NVIDIA nemotron (secondary fallback), DuckDuckGo search (`ddgs`), pytest (900+ offline tests), Render (free-tier hosting).

---

## 2. Five-Line Elevator Pitch

1. Distributors get a part number — we give back a complete, trustworthy product record.
2. Every attribute is extracted only from the manufacturer's own verified web pages, with a cited source attached to every value.
3. A deny-by-default trust engine blocks retailers, marketplaces, and look-alike sibling products before they can poison the data.
4. When proof is missing, we say NEEDS_REVIEW — never a plausible guess.
5. The result: a standards-compliant 252-column delivery row that a supply chain can actually rely on.

---

## 3. Feature List

| Feature | What it does |
|---|---|
| Evidence-first enrichment | Every value carries evidence refs (URL, title, timestamp); nothing is emitted source-less |
| Trusted manufacturer identity | Registry lookup by MPN/brand/manufacturer; run-local bootstrap with strong-evidence gates |
| Retailer/distributor rejection | Deny-by-default hostname policy; Amazon/eBay/Ace/Home Depot et al. rejected with recorded reasons |
| Sibling-product isolation | Pages referencing different MPNs from the same maker are excluded from extraction evidence |
| Claim-support validation | Extracted values must be supported by the cited snippet; unsupported claims rejected |
| LLM failover chain | Gemini (×5 key rotation) → Groq allam-2-7b → NVIDIA nemotron; typed retries/backoff |
| Groq + DuckDuckGo discovery | Free-tier search with provider fallback; no paid APIs |
| Guaranteed 252-column output | Schema-frozen delivery row on every run — success, partial, or failure |
| NEEDS_REVIEW behavior | Failed stages become review reasons, not crashes or guesses; human-in-the-loop ready |
| Batch processing | Chunked, resumable batch API with persisted progress (1000-row runs validated) |
| Evaluation harness | Token-guarded offline evaluation endpoint for regression checks without network/quota use |

---

## 4. Architecture Summary

```
Input (6 UniHack columns)
  → Identity        registry lookup → verified identity OR gated bootstrap
  → Discovery       DuckDuckGo (+fallback) candidate search
  → Trusted Policy  deny-by-default hostname allow-list; marketplace blocklist
  → Retrieval       SSRF-guarded HTML/PDF fetch, size/time limits
  → Evidence Sel.   exact-MPN presence + sibling contamination screen
  → AI Extraction   LLM w/ failover; evidence-cited attributes only
  → Claim Support   value-must-appear-in-snippet gate
  → Validation      UOM normalization, merge/dedup, conflict recording
  → Descriptions    8 evidence-grounded description types
  → Delivery        frozen 252-column CSV row + full provenance
```

Full detail: [`ARCHITECTURE.md`](ARCHITECTURE.md)

---

## 5. Demo Script

3–5 minute judge demo using `49-94-0013` (success path) and `DCB518ASTS06G` (safety/NEEDS_REVIEW path).
Full walkthrough: [`DEMO_SCRIPT.md`](DEMO_SCRIPT.md)

---

## 6. Known Limitations

Documented in full: [`LIMITATIONS.md`](LIMITATIONS.md). Headlines:
- Official UniHack LOV/UOM lists unavailable → those validations skipped, fields stay blank.
- Official taxonomy (Dept/Class/Fine/Classpath) unavailable → columns left blank, never guessed.
- Free-tier providers can hit quota → mitigated by failover/retry, not eliminated.
- Unsupported fields remain blank by design — correctness over completeness.

---

## 7. Final Tech Stack

| Layer | Technology |
|---|---|
| Backend | Python 3.11, FastAPI, Pydantic v2, Uvicorn |
| Database | SQLite via SQLAlchemy (Render persistent disk `/var/data`) |
| Frontend | React 18, TypeScript, Vite, Vitest |
| Primary LLM | Google Gemini (5-key rotation) |
| Fallback LLMs | Groq (allam-2-7b) → NVIDIA (nemotron) |
| Discovery | DuckDuckGo (`ddgs`), Groq-backed fallback |
| Retrieval | httpx, SSRF guard, TLS verification, size/time limits |
| Testing | pytest (900+ offline tests), Vitest |
| Deployment | Render (free tier), persistent disk |

---

## Package Contents

- `FINAL_PROJECT_SUMMARY.md` — problem, solution, differentiators, tech
- `ARCHITECTURE.md` — end-to-end pipeline explanation + worked examples
- `KEY_FEATURES.md` — feature deep-dives
- `DEMO_SCRIPT.md` — timed judge demo
- `JUDGE_TALKING_POINTS.md` — simple-language explanations
- `LIMITATIONS.md` — honest constraints
- `FINAL_CHECKLIST.md` — verification results (all pass)
