# PIN-TO-PIN FORENSIC AUDIT

Fresh inspection of the actual codebase on 2026-08-21.
Generated from direct code reads of every file in the repository.

---

## 1. PROJECT OVERVIEW

**Purpose:** AI-powered industrial product intelligence system for the UniHack hackathon. Takes limited product information (manufacturer, brand, part number, description) and produces a complete 252-column delivery row with evidence-backed attributes, commerce-ready descriptions, and full traceability.

**Input:** CSV with 6 columns: Mfg_Part_Num, Part_Desc, E1_Brand, Unilog_Brand, DIB_Brand, Part_Manuf

**Processing pipeline:** Input -> Identity (verified registry) -> Discovery (3 providers) -> SourcePolicy -> Ranking -> Retrieval (HTML/PDF, SSRF-guarded) -> Evidence Selection (sibling filter + budget) -> Extraction (LLM, evidence-only, P0 claim gate) -> Validation -> Descriptions (12 variants, grounding guard) -> 252-column Delivery -> SQLite Persistence

**Output:** 252-column CSV row per MPN (UTF-8 BOM, formula-injection guarded)

**Current deployment status:** Live on Render free tier at https://unihack-product-truth-engine.onrender.com with 1 GB persistent disk.

---

## 2. FILE/FOLDER STRUCTURE

### Backend (backend/)

| File | Lines | Purpose | Category |
|---|---|---|---|
| app/config.py | 233 | Pydantic Settings, all env vars, gemini_api_keys property | Config |
| app/main.py | 63 | FastAPI app, lifespan, CORS, SPA mount, 7 routers | Backend |
| app/pipeline/enrichment.py | 1221 | Core orchestration - 8 stages, deadline, failover chains | Backend |
| app/pipeline/base.py | 28 | Pipeline stage definitions | Backend |
| app/pipeline/manual_enrich.py | 200 | CLI for single-product enrichment | Backend/Tool |
| app/identity/mapping.py | 274 | Verified brand registry (37 entries), cross-check, placeholder detection | Backend |
| app/sources/discovery.py | 224 | Two-pass discovery orchestration | Backend |
| app/sources/policy.py | 163 | SourcePolicy: marketplace block, manufacturer trust | Backend |
| app/sources/ranking.py | 170 | Deterministic weighted ranking, English preference | Backend |
| app/sources/candidates.py | 78 | SourceCandidate model, normalize_domain | Backend |
| app/sources/errors.py | 40 | ProviderError hierarchy | Backend |
| app/sources/providers/search.py | 342 | Serper HTTP adapter, query builders | Backend |
| app/sources/providers/gemini_search.py | 375 | Gemini grounding adapter, 5-key rotation | Backend |
| app/sources/providers/groq_search.py | 362 | Groq compound-mini web search | Backend |
| app/sources/providers/manual_url.py | 86 | Direct URL input | Backend |
| app/sources/providers/manual_check.py | 88 | Manual source checking | Backend |
| app/sources/retrieval/html.py | 193 | HTMLParser text extraction | Backend |
| app/sources/retrieval/pdf.py | 103 | pypdf extraction | Backend |
| app/sources/retrieval/ssrf.py | 139 | SSRF guard (private IPs, DNS rebinding) | Backend/Security |
| app/sources/retrieval/transport.py | 129 | httpx async client, TLS | Backend |
| app/sources/retrieval/orchestrator.py | 99 | Retrieval orchestration | Backend |
| app/sources/retrieval/limits.py | 49 | Size limits | Backend |
| app/sources/retrieval/models.py | 102 | EvidenceRecord, RetrievalStatus | Backend |
| app/sources/retrieval/base.py | 37 | Base retrieval interface | Backend |
| app/llm/base.py | 272 | LLMClient ABC, factory, timeout, retry | Backend |
| app/llm/errors.py | 57 | LLMError hierarchy | Backend |
| app/llm/types.py | 97 | Request/response Pydantic models | Backend |
| app/llm/__init__.py | 59 | Public re-exports, provider registration | Backend |
| app/llm/providers/gemini.py | 213 | Gemini adapter, 5-key rotation on 429 | Backend |
| app/llm/providers/openrouter.py | 161 | OpenAI-compatible adapter (used for Groq) | Backend |
| app/llm/providers/nvidia.py | 161 | NVIDIA NIM adapter | Backend |
| app/llm/providers/deepseek.py | 160 | DeepSeek adapter | Backend |
| app/llm/providers/fake.py | 48 | Fake client (tests only) | Backend/Test |
| app/extraction/service.py | 509 | Extraction: failover, LLM-5 salvage, bullet fallback | Backend |
| app/extraction/prompt.py | 69 | Evidence-only extraction prompt | Backend |
| app/extraction/quotes.py | 213 | P0 claim-support gate (verbatim, MPN-anchored) | Backend |
| app/extraction/selection.py | 173 | Sibling filtering, context budget | Backend |
| app/extraction/types.py | 140 | ExtractionError, ExtractionOutput, CandidateAttribute | Backend |
| app/descriptions/service.py | 254 | Description generation, multi-provider failover | Backend |
| app/descriptions/rules.py | 221 | INVOICE/MOBILE rules | Backend |
| app/descriptions/grounding.py | 296 | Grounding guard (drops unsupported claims) | Backend |
| app/descriptions/types.py | 34 | GeneratedDescriptions schema | Backend |
| app/validation/service.py | 426 | Validation service (all stages) | Backend |
| app/validation/lov.py | 103 | LOV validation (STUB) | Backend |
| app/validation/uom.py | 87 | UOM validation (STUB) | Backend |
| app/validation/normalizer.py | 109 | Value normalization | Backend |
| app/validation/merge.py | 142 | Imperial/metric dedup | Backend |
| app/validation/vocab.py | 27 | Vocabulary checks | Backend |
| app/validation/manufacturer_brand.py | 86 | Manufacturer/brand validation | Backend |
| app/validation/types.py | 87 | Validation types | Backend |
| app/unihack/mapper.py | 354 | 252-column delivery mapper | Backend |
| app/unihack/schema.py | 204 | Frozen 252-column schema | Backend |
| app/unihack/parser.py | 195 | Input CSV parser | Backend |
| app/unihack/writer.py | 116 | CSV writer (UTF-8 BOM, formula guard) | Backend |
| app/unihack/models.py | 106 | DeliveryRow, UniHackInputRow | Backend |
| app/unihack/paths.py | 41 | Path resolution | Backend |
| app/unihack/delivery_headers.py | 271 | Frozen 252-column header artifact | Backend |
| app/db/repository.py | 719 | CRUD, freshness, cache isolation | Backend |
| app/db/models.py | 66 | SQLAlchemy Job + ProductRecordModel | Backend |
| app/db/database.py | 49 | Engine, session, init_db | Backend |
| app/db/migration.py | 100 | Idempotent SQLite column migration | Backend |
| app/evaluation/runner.py | 364 | Offline evaluation harness | Backend |
| app/evaluation/benchmark.py | 104 | Benchmark comparison | Backend |
| app/evaluation/__main__.py | 53 | CLI entry point | Backend |
| app/api/routes/enrich.py | 303 | POST /api/enrich endpoint | Backend |
| app/api/routes/batch.py | 381 | POST /api/batch endpoint | Backend |
| app/api/routes/health.py | 93 | GET /api/health + /api/health/llm | Backend |
| app/api/routes/dashboard.py | 143 | GET /api/dashboard | Backend |
| app/api/routes/evaluation.py | 87 | POST /api/evaluation/run (token-gated) | Backend |
| app/api/routes/downloads.py | 27 | GET /api/downloads/{name} | Backend |
| app/api/routes/lookup.py | 119 | GET /api/lookup | Backend |
| app/utils/retry.py | 41 | Exponential backoff retry helper | Backend |
| app/core/domain/*.py | ~400 | 13 typed domain model files | Backend |
| app/core/schemas.py | 30 | Core schemas | Backend |
| requirements.txt | 9 | 7 runtime deps + pytest (unpinned) | Config |

### Frontend (frontend/)

| File | Purpose | Category |
|---|---|---|
| src/App.tsx (~1200 lines) | Entire SPA: 3 tabs, state, API calls, result display | Frontend |
| src/main.tsx | Entry point | Frontend |
| src/styles.css | Tailwind + custom styles | Frontend |
| src/api/client.ts | Typed fetch wrapper | Frontend |
| src/api/types.ts | TypeScript interfaces | Frontend |
| dist/ | Pre-built production build (committed) | Frontend/Build |

### Config/Docs

| File | Purpose | Category |
|---|---|---|
| render.yaml (89 lines) | Render deployment manifest | Config |
| .gitignore (40 lines) | Git ignore patterns | Config |
| .env.example (112 lines) | Config template | Config |
| README.md (908 lines) | Full documentation | Docs |
| AUDIT.md | This file | Docs |

### Data

| File | Purpose | Category |
|---|---|---|
| backend/data/verified_brands.json | 37-entry verified brand registry (12 MPN + 17 brand + 9 manufacturer) | Data |
| backend/data/unihack.db | SQLite database (~35MB) | Data/Runtime |

### Tests (backend/tests/) - 44 files, 869 tests, ~16K lines

| File | Lines | Tests |
|---|---|---|
| conftest.py | 116 | Fixtures (autouse key blanking) |
| test_claim_support_gate.py | 480 | 25 |
| test_enrichment.py | 889 | 29 |
| test_extraction.py | 433 | 29 |
| test_extraction_failover.py | 575 | 20 |
| test_extraction_failover_chain.py | 467 | 16 |
| test_extraction_salvage.py | 365 | 19 |
| test_extraction_selection.py | 294 | 10 |
| test_description_failover_chain.py | 315 | 11 |
| test_descriptions.py | 169 | 10 |
| test_descriptions_rules.py | 103 | 11 |
| test_descriptions_salvage.py | 338 | 25 |
| test_validation.py | 547 | 55 |
| test_persistence.py | 1006 | 30 |
| test_batch_safety.py | 526 | 14 |
| test_unihack_delivery.py | 715 | 46 |
| test_unihack_input.py | 214 | 19 |
| test_search_provider.py | 630 | 44 |
| test_gemini_search_provider.py | 538 | 38 |
| test_groq_search_provider.py | 897 | 61 |
| test_source_discovery.py | 289 | 18 |
| test_discovery_recall.py | 215 | 10 |
| test_evidence_retrieval.py | 554 | 25 |
| test_ssrf_guard.py | 215 | 17 |
| test_transport_tls.py | 95 | 5 |
| test_llm.py | 221 | 24 |
| test_gemini_provider.py | 367 | 21 |
| test_nvidia_provider.py | 318 | 20 |
| test_openrouter_provider.py | 394 | 34 |
| test_deepseek_provider.py | 374 | 30 |
| test_identity_mapping.py | 204 | 17 |
| test_identity_reuse_safety.py | 176 | 5 |
| test_grounding.py | 431 | 13 |
| test_retry_backoff.py | 224 | 9 |
| test_health.py | 57 | 4 |
| test_evaluation.py | 89 | 2 |
| test_evaluation_api.py | 93 | 8 |
| test_evaluation_harness.py | 362 | 28 |
| test_api_extras.py | 354 | 14 |
| test_source_url.py | 490 | 20 |
| test_manual_url_provider.py | 109 | 7 |
| test_manufacturer_domain_trust.py | 261 | 6 |
| test_domain_models.py | 198 | 15 |
| test_csv_removal.py | 149 | 5 |

Note: test_gemini_live_smoke.py (135 lines) exists but has 0 pytest-collected tests - manual live script.

### Scripts

| File | Purpose | Category |
|---|---|---|
| backend/scripts/full_local_run.py | Resumable local batch driver (POST to localhost:8000) | Tool |
| backend/scripts/full_sample_run.py | Sample dataset runner | Tool |
| backend/scripts/inspect_db.py | DB inspection | Tool |
| backend/scripts/step15c_xlc10zw_live.py | Live test script | Tool |
| tools/eval_delivery.py | Offline delivery evaluator (485 lines) | Tool |

---

## 3. CURRENT FEATURES

### INPUT
- [x] 6-column CSV parser (UniHackInputParser)
- [x] Placeholder detection (6 tokens: -- Unbranded --, -- No Unilog Brand --, -- No DIB Brand --, -, empty MPN/description)
- [x] Duplicate MPN detection and grouping
- [x] Missing field tracking
- [x] Row error handling (wrong cell count -> error row, never dropped)

### IDENTITY
- [x] Verified brand registry (12 MPN + 17 brand + 9 manufacturer seeds)
- [x] Resolution priority: MPN seed -> brand seed -> manufacturer seed
- [x] Cross-check via seed_contradicted() - only resolved registry entries count as signals
- [x] Placeholder tokens never resolve to verified identity
- [x] Extra placeholders: COMMODITY - UNBRANDED, COMMODITY-UNBRANDED
- [x] Identity invariant gate: 5-point check (MPN, manufacturer, brand, domain, no placeholders) - fail-closed

### DISCOVERY
- [x] 3 providers: Serper (search), Gemini Grounding (gemini), Groq Web Search (groq)
- [x] Two-pass recall: Pass 1 query_biased=True; Pass 2 query_biased=False if zero ALLOWED
- [x] Provider selection via DISCOVERY_PROVIDER env (comma-separated)
- [x] Retry on ProviderUnavailableError with exponential backoff
- [x] Typed provider errors recorded, never fabricated

### SOURCE POLICY
- [x] Marketplace blocking: amazon, ebay, aliexpress, alibaba hostname labels
- [x] Manufacturer domain trust: per-product from registry
- [x] Configurable allowed/prohibited domain patterns
- [x] Permitted source types: 5 manufacturer types only
- [x] Every decision has human-readable rejection reason
- [x] Domain matching: case-insensitive, strips www., exact or subdomain

### RETRIEVAL
- [x] HTML: HTMLParser text extraction, metadata prepend, canonical URL
- [x] PDF: pypdf extraction, magic byte check (%PDF-)
- [x] SSRF guard: private IPs, DNS rebinding, metadata IPs, fail-closed
- [x] TLS: CERT_REQUIRED always, vendored GoDaddy G2 intermediate
- [x] Size limits: HTML 5MB, PDF 25MB, text cap 20,000 chars per record
- [x] Candidate cap: max 6 per product (RETRIEVAL_MAX_CANDIDATES)
- [ ] JavaScript rendering (SPA pages may yield empty)
- [ ] OCR for scanned PDFs
- [ ] Robots.txt compliance

### EVIDENCE
- [x] Sibling filtering (Step 20): MPN-token analysis, PRIMARY/SECONDARY/SIBLING classification
- [x] Context budget: EXTRACTION_CONTEXT_BUDGET_CHARS (default 12,000)
- [x] Evidence IDs bound to stored records
- [x] Full evidence set preserved for delivery; only LLM input filtered

### EXTRACTION
- [x] Evidence-only LLM prompt (12 explicit rules, system prompt forbids outside knowledge)
- [x] Mandatory evidence citations
- [x] P0 claim-support gate: deterministic, verbatim, MPN-anchored (100-char window)
- [x] Foreign product token filtering (>=5 chars, letter+digit, not requested MPN)
- [x] Confidence normalization: high->0.9, medium->0.6, low->0.3; bools rejected
- [x] LLM-5 salvage: per-item recovery from malformed JSON
- [x] Bullet-list fallback: strict regex
- [x] Multi-provider failover: ordered chain, timeout/unavailability triggers
- [x] Schema-invalid never triggers failover (by design)
- [x] MAX_CHARS_PER_RECORD = 6,000

### CLAIM SUPPORT (P0 GATE)
- [x] Deterministic verbatim occurrence check
- [x] MPN-anchored: value within CLAIM_MPN_WINDOW_CHARS=100 of MPN
- [x] Three-way ownership: requested (own passage), generic (family copy), foreign (sibling)
- [x] Foreign token regex: [A-Z0-9]+(?:-[A-Z0-9]+)*, >=5 chars, letter+digit
- [x] Quote window: 70 prefix + 90 suffix = 200 chars max
- [x] Regression tested: XLC10ZW fixture with 25 tests

### VALIDATION
- [x] Structural validation (required fields, types)
- [x] Evidence validation (claim traceability)
- [x] Value normalization (whitespace, case, special chars)
- [x] Imperial/metric dedup merge
- [ ] LOV validation: framework exists, stub only (needs official data)
- [ ] UOM validation: framework exists, stub only (needs official data)
- [ ] Vocabulary checks: minimal implementation
- [ ] Taxonomy classification: intentionally blank (no official data)
- [ ] Quality score formula: overall always 0.0 (no official formula)

### DESCRIPTION GENERATION
- [x] 12 variants: title, short, mobile, invoice, long, retail, marketing, features, with, application, includes, product_name
- [x] Grounding guard: drops unsupported claims
- [x] INVOICE rule: <=40 chars, ALL CAPS
- [x] MOBILE rule: 60-80 chars
- [x] LLM-7 salvage: per-field recovery
- [x] Multi-provider failover (same chain as extraction)
- [x] Item fields joined deterministically (; separator)

### DELIVERY
- [x] 252-column frozen schema (exact count, no blank headers, no duplicates)
- [x] SKU fallback: SKU - MY_PART_NUMBER = sku or mpn
- [x] Formula injection guard: =, +, @ always escaped; - conditionally
- [x] Input passthrough: 6 fields verbatim
- [x] MPN-aware URL mapping: exact > soft > sibling (never cited)
- [x] 50 attribute slots (label/value/uom triples)
- [x] UTF-8 BOM CSV output
- [x] MANUFACTURER_NAME from verified identity
- [x] BRAND_NAME from verified identity
- [ ] MANUFACTURER_PART_NUMBER (col 21): always blank
- [ ] Classification (Dept/Class/Fine/Classpath): intentionally blank with NOTE_TAXONOMY

### BATCH
- [x] Max 50 rows (BATCH_MAX_ROWS)
- [x] Row-level isolation (one failure never aborts batch)
- [x] Incremental persistence (commit per row)
- [x] Combined CSV download
- [x] Crash-safe: header written first, rows appended
- [x] Failed rows get honest blank delivery (exact width)
- [x] Path-traversal protected downloads

### DATABASE/PERSISTENCE
- [x] SQLite with SQLAlchemy ORM
- [x] jobs + product_records tables
- [x] 9-column Step 10B migration (idempotent, no Alembic)
- [x] FRESH/STALE/NOT_FOUND freshness verdicts
- [x] DB-first cache reuse with manufacturer-token isolation
- [x] X-Source: database / X-Stale: false headers
- [x] record_reuse counter (process-local, not persisted)

### CACHE
- [x] PRODUCT_CACHE_FRESHNESS_DAYS (default 30)
- [x] FRESH = last_enriched_at within window
- [x] Manufacturer compatibility check (_manufacturers_compatible)
- [x] No secrets serialized
- [x] Evidence text capped (batch_payload_evidence_cap_chars = 20,000)

### EVALUATION
- [x] Offline harness: reads CSV, runs pipeline, scores against expected output
- [x] Placeholder leak detection
- [x] Identity exact match
- [x] Invoice/MOBILE rule pass rates
- [x] Invoice length histogram
- [x] Token-gated evaluation endpoint (403 when unset, constant-time compare)
- [x] Per-row detail (discovery/retrieval/extraction/validation/descriptions metrics)
- [ ] Only 2 rows in expected output CSV (too small for comprehensive scoring)
- [ ] Attributes precision/recall: NOT_SCOREABLE (no ground truth)
- [ ] Classification LOV/UOM: NOT_SCOREABLE
- [ ] Part_number: NOT_SCOREABLE (distributor SKU)

### FRONTEND
- [x] 3-tab SPA: Single Product / Database / Batch
- [x] Quick MPN demo (prefilled XLC10ZW)
- [x] Advanced input: 6 fields + optional source URL
- [x] Load verified demo button
- [x] Use stored result if fresh checkbox
- [x] Result display: status badges, pipeline stages, identity, discovery, evidence, attributes with quotes, validation, descriptions, quality, delivery preview
- [x] Client-side CSV download (formula-injection guard)
- [x] Database dashboard: stats, recent MPNs, compliance
- [x] Batch: comma-separated MPNs, per-row status, combined CSV download
- [x] Race protection: runId = useRef(0)
- [ ] lookupMpn() API client defined but never called in UI
- [ ] No loading spinners on tab switches
- [ ] No pagination on batch results
- [ ] No error boundary
- [ ] No router (tab state via useState)

### SECURITY
- [x] SSRF guard: private IPs, DNS rebinding, metadata IPs, fail-closed
- [x] TLS: CERT_REQUIRED always, vendored GoDaddy G2 intermediate
- [x] Credentials backend-only (never sent to React)
- [x] Token-gated eval endpoint (hmac.compare_digest)
- [x] Path-traversal protected downloads
- [x] Batch hard limits (422 on excess)
- [x] No secrets in delivery output (__repr__ masks keys)
- [x] Formula injection guard in CSV
- [x] Cache isolation (manufacturer-token compatibility)
- [x] Evidence text capped to prevent SQLite growth

### DEPLOYMENT
- [x] Render free tier with 1 GB persistent disk
- [x] Health endpoints (/api/health, /api/health/llm)
- [x] Pre-built frontend SPA served same-origin
- [x] Pipeline deadline (180s hard cutoff)

### LLM/PROVIDER MANAGEMENT
- [x] 5 registered providers: gemini, deepseek, openrouter, nvidia, fake
- [x] Provider-agnostic LLMClient ABC
- [x] Multi-provider failover chain (extraction + descriptions)
- [x] Gemini 5-key rotation on 429
- [x] Retry/backoff: exponential, LLMProviderUnavailableError only
- [x] Wall-clock timeout via ThreadPoolExecutor(max_workers=8)
- [x] Health check with real LLM call (/api/health/llm)

---

## 4. CURRENT LLM SYSTEM

### Primary Provider
- Provider: gemini (env LLM_PROVIDER=gemini)
- Model: gemini-flash-lite-latest (env GEMINI_MODEL)
- Timeout: LLM_TIMEOUT_SECONDS=180 (code default 30.0)
- Adapter: GeminiClient in app/llm/providers/gemini.py
- Endpoint: https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent
- Auth: x-goog-api-key header
- Request style: {"contents": [{"role": "user", "parts": [{"text": prompt}]}]}
- Response parsing: candidates[0].content.parts[0].text -> JSON parse
- Multi-key rotation: 5 keys, rotates on 429 (see section 5)

### Fallback 1
- Provider: nvidia (env LLM_FALLBACK_PROVIDER=nvidia)
- Model: nvidia/nemotron-3.5-lightning-30b-a3b (env LLM_FALLBACK_MODEL)
- Timeout: LLM_FALLBACK_TIMEOUT_SECONDS=120
- Adapter: NvidiaClient in app/llm/providers/nvidia.py
- Endpoint: https://integrate.api.nvidia.com/v1/chat/completions
- Auth: Authorization: Bearer {NVIDIA_NIM_API_KEY}
- Request style: OpenAI-compatible chat/completions
- Response parsing: choices[0].message.content

### Fallback 2
- Provider: gemini (same as primary, reuse keys)
- Model: gemini-flash-latest (env LLM_FALLBACK_MODEL_2)
- Timeout: falls back to LLM_FALLBACK_TIMEOUT_SECONDS -> LLM_TIMEOUT_SECONDS
- Adapter: GeminiClient (same class, different model)

### Failover Chain
1. Primary: GeminiClient(gemini_api_keys, gemini-flash-lite-latest, 180s)
2. Fallback 1: NvidiaClient(NVIDIA_NIM_API_KEY, nemotron-3.5, 120s)
3. Fallback 2: GeminiClient(gemini_api_keys, gemini-flash-latest, per-fallback timeout)

### Retry Behavior
- LLM_RETRY_ATTEMPTS=2 (exponential: delay x 2^attempt = 1s, 2s)
- Only LLMProviderUnavailableError retried
- Timeouts and invalid responses NOT retried at this layer
- Gemini key rotation happens BEFORE the backoff layer
- Worst case: 2 x N HTTP requests before surfacing error

---

## 5. CURRENT GEMINI KEY ROTATION

### Verified from app/llm/providers/gemini.py (213 lines)

Env vars:
- GEMINI_API_KEY - single primary key
- GEMINI_API_KEYS - comma-separated list; WINS when set

Key loading (from app/config.py:208-221):
```python
@property
def gemini_api_keys(self) -> list[str]:
    raw = (self.GEMINI_API_KEYS or "").strip()
    if raw:
        keys = [k.strip() for k in raw.split(",")]
    else:
        keys = [self.GEMINI_API_KEY or ""]
    return [k for k in keys if k]
```

Number of keys: Arbitrary N. Rotation active only when N >= 2.

Rotation order: Fixed list order. Key[0] ALWAYS tried first on every call. On 429, iterates self._api_keys[1:]. No persistent round-robin state. Every new call restarts at key 0.

Trigger: ONLY response.status_code == 429 on the primary attempt.

Per-alternative-key behavior:
- HTTP 429 -> continue (try next key)
- HTTP 401 or 403 -> continue (skip bad key)
- Any other non-200 -> continue
- HTTP 200 -> adopt response, break

When ALL keys exhausted: for/else -> LLMProviderUnavailableError("rate limit hit on all configured API keys")

Applies to extraction: YES (ExtractionService receives primary client with rotation)
Applies to descriptions: YES (DescriptionsService receives same primary client)
Applies to discovery (Gemini search): YES (GeminiSearchApiClient has its OWN separate rotation loop in grounding_request())

---

## 6. CURRENT API KEYS

| Variable | Status | Used By | Purpose |
|---|---|---|---|
| LLM_PROVIDER | SET | config.py | Primary LLM provider selection |
| LLM_API_KEY | SET | OpenRouter/DeepSeek adapters | LLM API key |
| GEMINI_API_KEY | SET | GeminiClient, GeminiSearchApiClient | Primary Gemini key |
| GEMINI_API_KEYS | SET (5 keys) | Settings.gemini_api_keys | Multi-key rotation |
| GEMINI_MODEL | SET | GeminiClient | Model name |
| NVIDIA_NIM_API_KEY | SET | NvidiaClient | NVIDIA NIM key |
| SEARCH_PROVIDER_API_KEY | EXHAUSTED | SearchApiClient (Serper) | Serper credits depleted |
| GROQ_API_KEY | SET | GroqSearchApiClient | Groq web search key |
| EVALUATION_API_TOKEN | NOT SET | evaluation.py | Eval endpoint disabled (403) |
| LLM_FALLBACK_PROVIDER | SET | enrichment.py | First fallback (nvidia) |
| LLM_FALLBACK_MODEL | SET | enrichment.py | First fallback model |
| LLM_FALLBACK_MODEL_2 | SET | enrichment.py | Second fallback model |

---

## 7. CURRENT END-TO-END FLOW

User -> Frontend (React SPA :5173) -> POST /api/enrich -> EnrichmentService.run()

| Stage | Module | Function | Input | Output | Failure |
|---|---|---|---|---|---|
| Input | unihack/parser.py | UniHackInputParser.parse_text() | CSV text | UniHackInputResult | UniHackInputError (fatal) |
| Identity | identity/mapping.py | resolve_verified_identity() | MPN, brands, mfr | VerifiedIdentity | Blank fields, provenance |
| Discovery | sources/discovery.py | run_discovery() | ProductIdentity, providers | DiscoveryResult | Errors recorded, never fatal |
| SourcePolicy | sources/policy.py | SourcePolicy.filter() | candidates | (allowed, rejected) | Reasons recorded |
| Ranking | sources/ranking.py | rank_candidates() | allowed candidates | Ranked list | Never fails |
| Retrieval | sources/retrieval/orchestrator.py | retrieve_candidate() | candidates | EvidenceRecord list | FAILED records with error |
| Evidence Selection | extraction/selection.py | select_extraction_evidence() | identity, records, budget | SelectionResult | Dropped reasons |
| Extraction | extraction/service.py | ExtractionService.extract() | ExtractionRequest | ExtractionResponse | ExtractionError -> NEEDS_REVIEW |
| Claim Support | extraction/quotes.py | find_supported_quote() | text, values, mpn | (quote, anchored) | Empty quote -> rejected |
| Validation | validation/service.py | ValidationService.validate() | attributes, evidence_ids | ValidationSummary | NOT_VALIDATED (stubs) |
| Descriptions | descriptions/service.py | DescriptionsService.generate() | identity, attributes, evidence | Descriptions | LLM error -> NEEDS_REVIEW |
| Grounding | descriptions/grounding.py | apply_grounding() | descriptions, identity, attributes | Cleaned descriptions | Drops recorded |
| Rules | descriptions/rules.py | apply_description_rules() | descriptions | INVOICE/MOBILE compliant | Applied in-place |
| Mapper | unihack/mapper.py | UniHackDeliveryMapper.map() | ProductIntelligence, InputRow | DeliveryRow (252 cells) | Notes for blanks |
| Persistence | db/repository.py | ProductRepository.save_enrichment() | result, job_id | DB record | Rollback on error |
| Response | api/routes/enrich.py | enrich() | request | JSON EnrichmentResult | Sanitized 500 errors |

---

## 8. CURRENT DISCOVERY

Active providers: Groq (groq) - primary working provider
Configured but degraded: Serper (search) - credits exhausted; Gemini (gemini) - rate-limited
Provider selection: DISCOVERY_PROVIDER env (comma-separated, tried in order)

Two-pass logic:
1. Pass 1: query_biased=True -> site: hints toward manufacturer domains
2. If zero ALLOWED: Pass 2: query_biased=False -> wider recall
3. Results merged, deduplicated by URL, filtered by same SourcePolicy

Query builders:
- Pass 1 (build_search_query): manufacturer "MPN" brand (200-char cap)
- Pass 2 (build_recall_query): MPN specifications manufacturer

Ranking: Weighted score (policy_status 0.25, manufacturer_domain 0.25, source_type 0.15, part_number 0.15, relevance 0.10, trust_level 0.10). English pages preferred (0.02 penalty). Full determinism.

Marketplace rejection: amazon, ebay, aliexpress, alibaba hostname labels blocked.

---

## 9. CURRENT RETRIEVAL

| Parameter | Value | Source |
|---|---|---|
| HTML max size | 5,000,000 bytes (5MB) | RETRIEVAL_MAX_BYTES |
| PDF max size | 25,000,000 bytes (25MB) | RETRIEVAL_MAX_PDF_BYTES |
| Text cap per record | 20,000 chars | RETRIEVAL_MAX_TEXT_CHARS |
| Max candidates per product | 6 | RETRIEVAL_MAX_CANDIDATES |
| Retrieval timeout | 20s | RETRIEVAL_TIMEOUT_SECONDS |
| User agent | ProductTruthEngine/0.1 (hackathon) | RETRIEVAL_USER_AGENT |

HTML extraction: html.parser.HTMLParser (stdlib) - text only, metadata prepended.
PDF extraction: pypdf - text only. Magic byte check (%PDF-) before parse.
SSRF protection: Private IPs, DNS rebinding, metadata IPs. Fail-closed.
TLS: CERT_REQUIRED always. Vendored GoDaddy G2 intermediate.
JS rendering: NOT SUPPORTED
OCR: NOT SUPPORTED

---

## 10. CURRENT EVIDENCE SAFETY

Evidence IDs: EvidenceRecord.evidence_id - stable hash-based IDs. Bound to stored records via records_by_id dict.

Sibling filtering: MPN-token analysis (>=4 chars, contains digit).
- PRIMARY: MPN in URL or title
- SECONDARY: MPN in body only, or no foreign tokens
- SIBLING: Foreign product tokens present, no requested MPN reference

Context budget: EXTRACTION_CONTEXT_BUDGET_CHARS (default 12,000). Greedy fill PRIMARY->SECONDARY.

Claim-support gate guarantees:
- Every accepted attribute has a verbatim value occurrence in cited evidence
- Value is in the requested product's own passage or unattributable family copy
- Value NOT only near a sibling product's code
- Quote is anchored to the supporting occurrence
- Fully deterministic (no LLM, no fuzzy matching)

Claim-support gate does NOT guarantee:
- Semantic correctness of the quote (checks occurrence, not meaning)
- Completeness (attributes only found in retrieved evidence)
- Multi-word specs where components appear separately
- All attributes extracted (depends on evidence quality)

---

## 11. CURRENT EXTRACTION

Schema: ExtractionOutput -> items with: name, raw_value, normalized_value, unit, confidence (0.0-1.0), evidence_ids, notes

Prompt: 12 explicit rules, system prompt forbids outside knowledge, mandatory single-JSON output, per-record truncation at 6,000 chars

Confidence: Numeric 0.0-1.0 passes; None->0.0; high/medium/low -> 0.9/0.6/0.3; bools rejected

LLM-5 salvage: Per-item recovery from malformed JSON. Bad confidence -> reject only that item. Good items preserved verbatim. Salvaged items still pass P0 gate.

Bullet fallback: Strict regex "- name: value [ev-<id>]". Unknown evidence IDs dropped. Confidence stays 0.0.

Failover: LLMTimeoutError/LLMProviderUnavailableError -> try fallback chain. LLMInvalidResponseError -> salvage locally (never failover).

---

## 12. CURRENT DESCRIPTION SYSTEM

12 variants: product_title, short_description, mobile_description, invoice_description, long_description, retail_description, marketing_description, with, application, includes, product_name, features (first 20 -> ITEM_FEATURES_1..20)

LLM-7 salvage: Per-field recovery. Strings kept; None->""; lists joined with "; "; unknown keys ignored.

Grounding: apply_grounding() drops unsupported claims for certification, warranty, dimensions, material, performance, compatibility, accessory.

Rules: INVOICE <=40 chars ALL CAPS; MOBILE 60-80 chars.

---

## 13. CURRENT VALIDATION

What IS validated: Structural correctness, evidence traceability, value normalization, imperial/metric dedup merge.

What remains NOT_VALIDATED: All LOV values, all UOM values, taxonomy classification. By design - no false VERIFIED without official data.

---

## 14. CURRENT 252-COLUMN DELIVERY

Schema: Frozen artifact in delivery_headers.py (SHA256 3304b26f..., 252 exact headers).
Column count: Exactly 252. Validated: rejects wrong count, blank headers, duplicates.
Mapper: UniHackDeliveryMapper (354 lines). One-way: ProductIntelligence -> DeliveryRow.

Key mappings:
- MFR URL: Evidence (exact/soft MPN match, manufacturer page only)
- PART_NUMBER: identity.mpn (from verified registry)
- SKU - MY_PART_NUMBER: identity.sku or fallback to identity.mpn
- Mfg_Part_Num/Part_Desc/E1_Brand/Unilog_Brand/DIB_Brand/Part_Manuf: Input passthrough (verbatim)
- MANUFACTURER_NAME/BRAND_NAME/TRADE_NAME: Verified identity
- MANUFACTURER_PART_NUMBER: Always blank
- MOBILE_DESC/INVOICE_DESC/SHORT_DESC/LONG_DESC1/RETAIL_DESC/MARKETING_DESCRIPTION: Generated
- ITEM_FEATURES_1..20: Generated (first 20 features)
- ATTRIBUTE_LABEL/VALUE/UOM n: Extracted attributes (50 slots)
- Dept/Class/Fine/Classpath: Blank + NOTE_TAXONOMY

---

## 15. CURRENT BATCH

Max batch size: 50 rows (BATCH_MAX_ROWS=50, HTTP 422 on excess)
Row isolation: One failing row -> FAILED record with sanitized error. Batch continues.
Incremental persistence: Header written first, each row committed individually.
Combined CSV: data/batch/batch-{timestamp}-{uuid8}.csv
Latest results: 1000-row run in 11.7 min (Groq LLM). 20-row Render batch: 4/20 in 113s.

---

## 16. CURRENT DATABASE/CACHE

Database: SQLite 3 (SQLAlchemy ORM)
Tables: jobs, product_records
Freshness: FRESH/STALE/NOT_FOUND (default 30 days)
Cache isolation: _manufacturers_compatible() - per-product manufacturer token check
Persistent storage: /var/data/unihack.db on Render

---

## 17. CURRENT FRONTEND

Tab 1 (Single Product): Quick MPN demo, advanced 6-field input, result display, CSV download
Tab 2 (Database): Dashboard stats, compliance section
Tab 3 (Batch): Comma-separated MPNs, per-row status, combined CSV download

API calls: getHealth, enrichOne, lookupMpn (unused), getDashboard, runEvaluation, runBatch

Limitations: No loading spinners, no pagination, no error boundary, no router, lookupMpn unused

---

## 18. CURRENT SECURITY

- SSRF guard: Private IPs, DNS rebinding, metadata IPs, fail-closed
- TLS: CERT_REQUIRED always, vendored GoDaddy G2 intermediate
- Credentials: Backend-only, never sent to React, __repr__ masks keys
- API auth: Token-gated eval endpoint, hmac.compare_digest
- Path traversal: Downloads restricted to batch directory
- CSV injection: escape_formula() for =, +, @
- Cache isolation: Manufacturer-token compatibility
- Identity invariant: 5-point check, fail-closed
- Batch limits: Hard cap 50, HTTP 422

---

## 19. CURRENT EVALUATION SYSTEM

Scoreable: mpn_identity, manufacturer_name, brand_name, part_desc, description_completeness (live only), mfr_url_relevance, mpn_isolation
NOT_SCOREABLE: part_number, attributes_precision_recall, classification_lov_uom
Ground truth: Only 2 rows in expected output CSV
Status: Framework works. 28 tests. EVALUATION_API_TOKEN not set locally.

---

## 20. CURRENT TEST SUITE

Total: 869 pytest functions, 44 files, ~16K lines, 1 skipped
0 tests call any external API (conftest.py blanks all keys)

Key suites:
- 25 claim-support tests (XLC10ZW regression)
- 29 enrichment tests (full pipeline)
- 59 Gemini rotation tests (21 provider + 38 search)
- 56 LLM failover tests (20+16+11+9)
- 47 retrieval tests (25+17+5)
- 82 security-related tests (17 SSRF + 5 TLS + 14 batch + 46 delivery)
- 65 delivery tests (46+19)
- 30 persistence tests
- 14 batch safety tests

---

## 21. CURRENT DEPLOYMENT

HEAD: 370a9be ("feat: rotate multiple Gemini API keys on rate limits (x5 throughput)")
Branch: main, tracking origin/main
Tracked modifications: None

Render: product-truth-engine, free tier, oregon, Python 3.11
URL: https://unihack-product-truth-engine.onrender.com
Persistent disk: 1 GB at /var/data
Health: GET /api/health

Config divergence:
- render.yaml: DISCOVERY_PROVIDER=search,gemini, budget 8000
- local: DISCOVERY_PROVIDER=groq, budget 20000
- render.yaml lacks GEMINI_API_KEYS multi-key documentation

---

## 22. CURRENT PRODUCTION RESULTS

XLC10ZW (Makita): completed - 9 attributes, all with verbatim quotes, description generated
XLC02ZW/XLC03ZBX4/XLC05ZWX4/XLC10R1W/XLC11ZW: needs_review (weak discovery for some)
PDSH4816AF (Frigidaire): needs_review
49-94-0013/49-94-2000 (Milwaukee): needs_review
1700-1PK-BB40 (3M): needs_review
WDTS7024RZ (Whirlpool): needs_review (sibling pages only, no extraction)
1000-row run: 860 completed, 123 identities, 85 attributes, 83 descriptions, 11.7 min
20-row Render batch: 4/20, 113s

---

## 23. CURRENT LIMITATIONS

### A. Real Code Limitations
1. lookupMpn() frontend API client defined but never called
2. No MANUFACTURER_PART_NUMBER (col 21) mapping - always blank
3. Frontend is single-file App.tsx (~1200 lines)
4. No error boundary in React
5. All Python dependencies unpinned in requirements.txt
6. .env header comment stale (references "OpenRouter" but values point to Gemini/Groq)

### B. Provider Limitations
1. Gemini free-tier rate limits: all 5 keys can hit 429 simultaneously
2. Serper credits exhausted
3. Groq search weaker than Gemini/Serper for some MPNs
4. NVIDIA Nemotron never validated end-to-end
5. No JavaScript rendering

### C. Missing Organizer Resources
1. Official LOV values
2. Official UOM standards
3. Official taxonomy (Dept/Class/Fine/Classpath)
4. Official quality formula
5. Expected output: Only 2 rows

### D. Data Limitations
1. User's real MPN CSV not provided
2. WDTS7024RZ mislabeled as Frigidaire in sample dataset

### E. Deployment Limitations
1. Render free tier: 15-min spin-down, 512MB RAM
2. render.yaml not synced with local config
3. SQLite: no concurrent writes

### F. UX Limitations
1. Backend must run separately from frontend
2. No progress indicator during batch runs
3. No streaming
4. No result history in UI

---

## 24. CURRENT GIT STATE

HEAD: 370a9be ("feat: rotate multiple Gemini API keys on rate limits (x5 throughput)")
Branch: main (single branch)
Tracked modifications: None

Untracked files: AUDIT.md, organizer PDFs/CSVs, proof scripts, tools/, test_evaluation_harness.py

---

## 25. SCORECARD

| Dimension | Rating | Reason |
|---|---|---|
| Architecture | 9/10 | Clean 8-stage pipeline, provider-agnostic LLM, 869 offline tests |
| Correctness | 8/10 | No fabrication, evidence-bound only, P0 gate proven on XLC10ZW |
| Evidence quality | 7/10 | Sibling filtering works; Groq search sometimes weak |
| Security | 9/10 | SSRF, TLS, credential isolation, token-gated eval, formula guard |
| Discovery | 7/10 | 3 providers; Serper exhausted, Gemini rate-limited |
| Retrieval | 7/10 | HTML+PDF with SSRF; no JS rendering, no OCR |
| Extraction | 8/10 | Evidence-only prompt, P0 gate, LLM-5 salvage, 3-provider failover |
| Claim support | 9/10 | Deterministic verbatim MPN-anchored gate, 25-test regression |
| Descriptions | 7/10 | 12 variants, grounding guard; depends on extraction success |
| Validation | 5/10 | Framework complete; LOV/UOM stubs only |
| Delivery | 9/10 | 252 columns exact, formula guard, SKU fallback, MPN-aware URLs |
| Batch | 7/10 | Row isolation, crash-safe; synchronous, no progress indicator |
| LLM reliability | 8/10 | 3-provider failover, Gemini 5-key rotation; NVIDIA unvalidated |
| Frontend | 7/10 | 3 tabs working; single-file, no router, no error boundary |
| Evaluation | 6/10 | Offline harness works; only 2 expected rows |
| Deployment | 8/10 | Render free tier, persistent disk; render.yaml not synced |
| Hackathon readiness | 8/10 | End-to-end working, 1000-row proven, honest limitations |

---

## 26. FINAL ANSWER

### 1. WHAT WE HAVE BUILT
An AI-powered industrial product intelligence system that takes 6 fields and produces a 252-column delivery row. Discovers manufacturer sources via 3 providers, retrieves evidence (SSRF-guarded), extracts attributes via LLM with mandatory citations and a deterministic P0 claim-support gate, generates 12 description variants with grounding guard, and maps to the official UniHack format. Runs on Render with 1GB persistent disk. 869 offline tests. 1000-row production run in 11.7 minutes. No fabricated URLs, no unsupported claims, honest failure modes.

### 2. CURRENT FILE STRUCTURE
See Section 2 (~104 backend files, ~17 frontend files, 44 test files, config/docs, data, scripts).

### 3. CURRENT FEATURE LIST
See Section 3 (60+ features across 19 categories with implementation status).

### 4. CURRENT LLM/API ARCHITECTURE
See Section 4 (Gemini primary -> NVIDIA fallback 1 -> Gemini flash-latest fallback 2).

### 5. CURRENT GEMINI KEY ROTATION
See Section 5 (5 keys in GEMINI_API_KEYS, fixed order, key[0] always first, rotation on 429).

### 6. CURRENT PRODUCTION RESULTS
See Section 22 (XLC10ZW completed; 1000-row run completed).

### 7. CURRENT TEST STATUS
See Section 20 (869 tests, 44 files, 0 external API calls).

### 8. CURRENT LIMITATIONS
See Section 23 (6 categories of limitations).

### 9. CURRENT DEPLOYMENT
See Section 21 (Render free tier, HEAD 370a9be, persistent disk).

### 10. WHAT IS STILL MISSING
1. Official LOV/UOM/taxonomy data (organizer dependency)
2. User's real MPN CSV
3. EVALUATION_API_TOKEN not set locally
4. render.yaml not synced with local config
5. NVIDIA Nemotron never validated
6. Dependencies unpinned in requirements.txt

### 11. WHAT MUST NOT BE TOUCHED BEFORE SUBMISSION
- app/extraction/quotes.py (P0 claim-support gate)
- app/sources/retrieval/ssrf.py (security-critical)
- app/pipeline/enrichment.py (core orchestration)
- app/unihack/mapper.py (252-column mapping)
- app/unihack/schema.py (frozen schema)
- app/unihack/delivery_headers.py (frozen headers)
- backend/data/verified_brands.json (registry)
- backend/tests/test_claim_support_gate.py (regression suite)
- backend/tests/test_enrichment.py (pipeline regression)
- backend/tests/test_unihack_delivery.py (delivery tests)
- app/llm/providers/gemini.py (5-key rotation)
- app/sources/providers/gemini_search.py (Gemini search rotation)
- render.yaml (deployment manifest)
- frontend/dist/ (pre-built SPA)
- .env (live API keys - never commit)

---

End of PIN-to-PIN forensic audit.
