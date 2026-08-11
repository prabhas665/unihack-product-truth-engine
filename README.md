# Product Truth Engine

Hackathon project for the UniHack **AI-Powered Product Intelligence** challenge.

An AI-powered industrial product intelligence system that takes limited/messy product
information (manufacturer, brand, part number, description), discovers permitted
manufacturer sources, extracts product information, normalizes and validates it,
attaches evidence, detects conflicts, computes confidence and quality scores,
generates commerce-ready descriptions, and lets the user download the final result.

> **Status: repository foundation only.** This is a clean, modular skeleton.
> No product intelligence functionality is implemented yet, by design.

## Architecture

```
frontend/ (React + Vite + TypeScript SPA)
   |  HTTP /api/* (Vite dev proxy -> :8000)
   v
backend/  (FastAPI, Python 3.11)
   app/
     api/         REST routes: health, lookup, batch, dashboard, downloads
     pipeline/    orchestration: discovery -> extraction -> normalization
                  -> validation -> description (stage registry)
     sources/     source discovery (candidates, policy, deterministic ranking)
                 + evidence retrieval (HTML/PDF fetchers, limits, security gates)
     llm/         provider-agnostic LLM client (LLMClient + typed ops + registry)
     validation/  LOV validation, UOM normalization, quality gates
     db/          SQLAlchemy models + SQLite persistence (jobs, product records)
     export/      Excel + JSON result downloads
     core/        domain models (app.core.domain) and API schemas
```

### Module map

| Module | Role | Implemented |
|---|---|---|
| `backend/app/api/routes/health.py` | liveness check | **yes** |
| `backend/app/core/` | internal domain model (`ProductIntelligence` and friends, Pydantic) | **yes** |
| `backend/app/pipeline/` | stage registry + `Stage` protocol; stages will be added per feature | interface only |
| `backend/app/sources/` | candidates + policy + ranking + provider registry + evidence retrieval (HTML/PDF) | **yes** (no network in tests) |
| `backend/app/llm/` | provider-agnostic `LLMClient` + typed ops + errors + offline `fake` provider | **yes** |
| `backend/app/extraction/` | evidence-based attribute extraction (prompt builder, service, typed models) | **yes** (offline tests only) |
| `backend/app/validation/` | normalization + validation framework: Normalizer, LOV/UOM/manufacturer-brand provider abstractions (unavailable until official data loads), ValidationService | **yes** (framework only; no official data) |
| `backend/app/sources/providers/` | real search discovery provider behind `SourceProvider` (Serper-style JSON API; typed errors; env-only config) + manual integration check | **yes** (Step 6B; offline tests only) |
| `backend/app/llm/providers/deepseek.py` | real DeepSeek provider adapter behind `LLMClient` (official chat/completions API; typed errors; env-only config) | **yes** (Step 6C; offline tests only) |
| `backend/app/unihack/` | real UniHack dataset integration: input CSV parser, 252-column delivery schema (loaded from the official reference file), delivery mapper, delivery CSV writer | **yes** (Step 6A; no AI/discovery/batch yet) |
| `backend/app/db/` | SQLite engine + minimal ORM tables | schema only |
| `backend/app/export/` | export package | placeholder only |
| `frontend/` | SPA shell that calls `/api/health` | shell only |

### Design principles

- **Evidence-based only.** No attribute is emitted without attached evidence.
- **No hallucinated attributes.** The AI layer is a thin abstraction; nothing calls an LLM yet.
- **Permitted sources only.** Manufacturer websites and manufacturer documentation are
  preferred; marketplaces/distributors (Amazon, eBay, ...) are out of scope by rule.
- **Controlled vocabularies plug in later.** The official UniHack LOV values, UOM rules
  and quality gates are NOT known yet and are **not** fabricated here.
- **Provider-agnostic AI.** Swap LLM providers via `.env` without touching the pipeline.

## Repository layout

```
backend/          FastAPI application (Python 3.11)
frontend/         React + Vite + TypeScript SPA
data/             runtime data (SQLite DB, future uploads) - gitignored
.env.example      template for environment configuration
```

## Setup

Create the virtual environment once (from the repo root):

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r backend\requirements.txt
```

### Backend

```powershell
uvicorn app.main:app --reload --port 8000
```

Check it is alive: `GET http://127.0.0.1:8000/api/health`

### Frontend

```powershell
cd frontend
npm install
npm run dev
```

Open `http://127.0.0.1:5173` (dev server proxies `/api` to the backend on :8000).

### Tests

```powershell
.\.venv\Scripts\Activate.ps1
pytest backend\tests
```

No API key is needed: the LLM tests use the built-in offline `fake` provider
(`LLM_PROVIDER=fake`) and canned responses. No test ever calls an external
LLM API.

## LLM provider abstraction

### Why it exists

The enrichment pipeline must not depend on a specific AI vendor. Everything
in `backend/app/llm/` talks to one interface - `LLMClient` - with typed
request/response models:

- `extract()` - structured attribute extraction (returns `ExtractedAttributes`,
  which maps onto the domain `AttributeValue`)
- `classify()` - classification into the domain `Classification` model
- `generate_description()` - commerce-ready description variants
- `structured_completion()` - arbitrary structured output against any Pydantic
  schema
- `complete()` - free-form completion

Providers implement **only** `_complete()` (the raw vendor call: endpoint,
auth, model name). JSON parsing, schema validation, and error mapping
(missing config, provider unavailable, timeout, malformed/invalid output)
happen once in the base class. Errors are typed: `LLMConfigurationError`,
`LLMProviderUnavailableError`, `LLMTimeoutError`, `LLMInvalidResponseError`.

### Configuration

| Env var | Purpose |
|---|---|
| `LLM_PROVIDER` | provider name, must match a registered adapter (`fake` is built in; `deepseek` is the real adapter, Step 6C) |
| `LLM_API_KEY` | backend-only secret for real providers (never send it to the frontend, never commit it) |
| `LLM_MODEL` | optional model override; empty = provider default (`deepseek-chat` for deepseek) |
| `LLM_BASE_URL` | optional API base URL override (empty = provider default endpoint; point it at a local mock/proxy to test the adapter offline) |
| `LLM_TIMEOUT_SECONDS` | default call timeout (default 30s) |

The application starts without any of these set - `get_client()` is lazy and
only fails when actually called with no provider configured (typed
`LLMConfigurationError`), never at startup.

### The real DeepSeek adapter (Step 6C)

`backend/app/llm/providers/deepseek.py` implements the official DeepSeek
chat/completions API (OpenAI-compatible: `POST /chat/completions`,
`Authorization: Bearer <key>`, `{model, messages, temperature}`) behind
`LLMClient` - it implements **only** `_complete()`, so all four operations
(extraction, classification, description generation, generic structured
completion) work through it unchanged, including JSON parsing, markdown-fence
tolerance, and schema validation in the base class.

Failure mapping (all typed, never crashing the application):

| Situation | Error |
|---|---|
| missing/blank API key | `LLMConfigurationError` (lazy, at call time) |
| timeout | `LLMTimeoutError` |
| network/connection failure | `LLMProviderUnavailableError` |
| HTTP 401/403 (authentication) | `LLMProviderUnavailableError` (message names the status code, never the key) |
| HTTP 429 (rate limit) | `LLMProviderUnavailableError` |
| other non-200 / provider outage | `LLMProviderUnavailableError` |
| malformed JSON / missing `choices[0].message.content` | `LLMInvalidResponseError` |
| output failing schema validation | `LLMInvalidResponseError` (base class) |

**Evidence-first extraction is unchanged and structurally enforced**: the
adapter has no browsing/search/tool access - it is a single HTTP POST that
can only answer from the prompt `ExtractionService` builds from the supplied
evidence. The service still rejects every accepted attribute lacking valid
`evidence_ids`, exactly as before; the Step 6C tests re-verify this end to
end through the real adapter (evidence text and id present in the sent
prompt, preserved on accepted candidates, dangling/missing ids rejected).

### Adding another provider (e.g. OpenAI, Anthropic)

1. Create `backend/app/llm/providers/<name>.py` subclassing `LLMClient`,
   set `provider = "<name>"`, and implement only `_complete()` (reading
   `LLM_*` config from `app.config.settings`).
2. Register it in `backend/app/llm/providers/__init__.py`:
   `register_provider("<name>", <Name>Client.from_settings)`.
3. Set `LLM_PROVIDER=<name>` and the provider's `LLM_*` env vars in `.env`.

No pipeline or API code changes are needed - the application only ever sees
`LLMClient`.

### Security notes (Step 6C)

- `LLM_API_KEY` is **backend-only**: it is read exclusively from environment
  variables, never from source code, never sent to React, and never logged.
  Error messages and `repr()` contain status codes and URLs only.
- `.env.example` ships placeholder/empty values - a real key is never
  committed.
- The provider makes no outbound calls beyond the single configured
  chat/completions endpoint - the model cannot browse or search.

### OpenCode's coding model vs the application's runtime LLM

The model running THIS assistant (OpenCode) is unrelated to the
application's runtime LLM. The application calls whatever is configured in
the backend `.env` (`LLM_PROVIDER`/`LLM_API_KEY`) through `app.llm` - for
example `LLM_PROVIDER=deepseek` with the DeepSeek adapter. OpenCode's own
coding model and API key live in your local OpenCode configuration and are
never touched by this repository.

### Running tests without an API key

```powershell
pytest backend\tests
```

The suite never touches the network: LLM tests inject canned responses and
errors via `FakeLLMClient` (`LLM_PROVIDER=fake`, no key required) and the
DeepSeek adapter tests use `httpx.MockTransport` with canned JSON - the real
DeepSeek API is **never** called during tests.

## Evidence-based extraction (`backend/app/extraction/`)

Turns retrieved `EvidenceRecord`s into candidate product attributes, keeping
the "no attribute without evidence" rule structurally enforced:

- `prompt.py` - deterministic prompt builder; the system prompt tells the
  model it has **no knowledge outside the supplied evidence**, and the rules
  forbid guessing and require citing `evidence_ids` for every claim.
- `types.py` - `ExtractionRequest` (identity + raw description + ≥1
  `EvidenceRecord`), the AI-facing `ExtractionOutput` schema, and
  `ExtractionResponse` (accepted `CandidateAttribute`s + `RejectedAttribute`s
  with reasons).
- `service.py` - `ExtractionService.extract()`: validates the LLM's output
  semantically (every accepted attribute must cite at least one known
  evidence id; empty claims rejected), maps provider failures to typed
  `ExtractionError`s, and caps note length. Conflicts are **not** resolved
  here - multiple candidates with the same name are kept for the validation
  stage. `to_domain_attribute_values()` maps the result onto the existing
  domain model (with `ConflictStatus.CONFLICT` when evidence disagrees).

All extraction tests are offline: `FakeLLMClient` returns canned JSON, so no
real LLM API and no network are ever touched.

## Normalization and validation framework (`backend/app/validation/`)

A modular, provider-based framework that is READY for the official UniHack
resources to be plugged in later. It never claims an attribute is officially
Unilog-valid: until official data is loaded, providers report "Official
UniHack LOV/UOM/manufacturer-brand data not loaded." and nothing is
VERIFIED.

```
CandidateAttribute (evidence-bound, from extraction)
      ↓
ValidationService
  ├─ structural checks (name/value/confidence/unit sanity)
  ├─ evidence-reference checks (no missing/dangling evidence ids)
  ├─ normalization (deterministic; original raw_value always preserved)
  ├─ vocabulary/LOV check   ── only when a VocabularyProvider is AVAILABLE
  └─ UOM check              ── only when a UOMProvider is AVAILABLE
      ↓
ValidatedAttribute → VERIFIED | NEEDS_REVIEW | NOT_VALIDATED | INVALID
```

- `normalizer.py` - `Normalizer` protocol + `DefaultNormalizer`. Implemented
  now: generic text cleanup and fraction→decimal conversion (pure math).
  NOT implemented (needs official files): manufacturer/brand aliasing, UOM
  canonicalization, category-specific rules - those return input unchanged
  and are never invented.
- `lov.py` - `VocabularyProvider` / `LOVProvider` protocol (find allowed
  attribute, validate value, normalize value, applicable values per
  classpath) + `UnavailableVocabularyProvider` reporting
  `"Official UniHack LOV data not loaded."`
- `uom.py` - `UOMProvider` protocol (unit lookup, unit normalization,
  measurement-type validation) + `UnavailableUOMProvider`. No approved UOM
  abbreviations are invented.
- `manufacturer_brand.py` - `ManufacturerBrandProvider` protocol (matching +
  canonical names) + `UnavailableManufacturerBrandProvider`.
- `types.py` - `ValidationOutcome` (VERIFIED / NEEDS_REVIEW / NOT_VALIDATED /
  INVALID), `Severity`, `ValidationMessage` (stable codes + WHY text),
  `ValidatedAttribute` (original raw_value + normalized_value + preserved
  evidence_refs), `ValidationSummary`.
- `service.py` - `ValidationService` combining all checks. Verdict rules
  guarantee no false VERIFIED results: hard errors → INVALID; warnings →
  NEEDS_REVIEW; only all-pass against AVAILABLE official resources →
  VERIFIED; otherwise NOT_VALIDATED. `to_domain_attribute_value()` maps onto
  the existing domain model (`AttributeStatus.VALIDATED/NEEDS_REVIEW/
  EXTRACTED/REJECTED`).
- `vocab.py` - still-empty data containers (LOV/UOM/manufacturer/brand
  dicts) where the official resources can be loaded; providers can be backed
  by them or by the raw resource files.

Future official sources plug in as provider adapters, no redesign needed:
UNILOG_INTERNAL_CONTENT_GUIDELINES.docx, Unilog Master UOM Standards,
Decimal_Fraction.xlsx, UniCat Manufacturer/Brand List, Unicat LOV,
FAUCETS_LOV, Fittings_LOV. None are present in the repo and none are
parsed or fabricated.

## Source discovery foundation

### Flow

```
Product identity ──► discovery providers ──► source policy ──► ranking
                          (registry)            (filter)        (deterministic)
                                                    │
                                    allowed ──► ranked candidates (for retrieval)
                                    rejected ──► kept with rejection_reason (for review)
```

### Modules (`backend/app/sources/`)

| Module | Role |
|---|---|
| `candidates.py` | `SourceCandidate` (URL, title, `SourceType`, domain, manufacturer relationship, trust level, `relevance_score`, `DiscoveryMethod`, `CandidateStatus`, `rejection_reason`) + `normalize_domain()` |
| `policy.py` | `SourcePolicy` + `SourcePolicyConfig`: marketplace labels (Amazon/eBay/...) rejected, manufacturer-owned domains preferred, source types whitelisted, decisions always record WHY |
| `ranking.py` | `rank_candidates()`: deterministic weighted score (policy status, manufacturer-domain match, source type, exact part-number presence, title/URL relevance, trust level). No LLM. |
| `discovery.py` | `SourceProvider` protocol + registry, `DiscoveryContext`, `run_discovery()` orchestration, `DiscoveryResult` (ranked allowed + rejected-for-review) |
| `retrieval/` | evidence retrieval: `EvidenceRecord`, limits, HTML + PDF fetchers, orchestrator (see below) |

### Policy behavior

- **Prohibited**: hostname labels matching the built-in marketplace set
  (`amazon`, `ebay`, `aliexpress`, `alibaba` - exact-label matching, so e.g.
  `amazonaws.com` hosting is not caught), plus any
  `SOURCE_PROHIBITED_DOMAINS` patterns.
- **Preferred**: domains in the manufacturer's owned-domain registry
  (empty until official UniHack manufacturer data exists) with a permitted
  source type (`MANUFACTURER_PRODUCT_PAGE`, `MANUFACTURER_TECHNICAL_PDF`,
  `MANUFACTURER_MANUAL`, `MANUFACTURER_CATALOGUE`,
  `MANUFACTURER_DIGITAL_ASSET`).
- **Rejected**: unknown external domains, and any `UNKNOWN` source type
  (unsupported types are never silently allowed).
- Everything is configurable via `SOURCE_ALLOWED_DOMAINS` /
  `SOURCE_PROHIBITED_DOMAINS` (comma-separated domains in `.env`); no
  UniHack data is hard-coded.

### Providers (future)

`SourceProvider` is the single interface; planned implementations map onto
`DiscoveryMethod` values: search provider (`SEARCH`), direct manufacturer URL
provider (`DIRECT_URL`), document provider (`DOCUMENT`). They register via
`register_provider()` and are exercised by `run_discovery()`. **None are
implemented yet and nothing makes network calls.**

### Test fixtures

All source tests use explicitly labeled made-up fixtures
(`acme-controls.example`, `random-shop.example.com`, ...) - they are test
data, never UniHack data or real manufacturers. No test touches the network.

## Evidence retrieval foundation

### Flow

```
SourceCandidate (status = ALLOWED, passed SourcePolicy)
        │  policy gate (rejected candidates → SKIPPED record, never fetched)
        │  scheme gate (only http/https; file:/ftp:/... → UNSAFE_URL)
        v
  Fetcher (HTML or PDF, chosen by source type)
        │  timeout + size cap enforced while streaming
        v
Retrieved document → EvidenceRecord (content + status + error metadata)
        │
        v
  to_domain_evidence() → domain Evidence (used by the future pipeline)
```

### Modules (`backend/app/sources/retrieval/`)

| Module | Role |
|---|---|
| `models.py` | `EvidenceRecord` (id, candidate id, URL, final URL, source type, title, text, content type, timestamps, `RetrievalStatus`, `ExtractionStatus`, error kind/message, metadata) + typed `RetrievalError` |
| `limits.py` | `RetrievalLimits` (timeout, HTML size cap, PDF size cap, user agent) from settings |
| `transport.py` | shared size-capped streaming download with typed error mapping |
| `html.py` | `HtmlFetcher` - httpx + stdlib HTMLParser; keeps `<title>`, canonical URL, drops script/style |
| `pdf.py` | `PdfFetcher` - httpx + pypdf; validates the `%PDF-` signature; reports extraction failure (e.g. scanned PDFs) |
| `orchestrator.py` | `retrieve_candidate()` - policy gate, scheme gate, fetcher dispatch, HTML→PDF fallback for catalogue/unknown |

### Supported formats

| Format | Detection | Extraction |
|---|---|---|
| HTML | content-type `text/html`, `application/xhtml+xml`, `text/plain` | stdlib HTMLParser readable text (no JS rendering) |
| PDF | `%PDF-` signature (content-type mismatch tolerated if signature matches) | pypdf text layer |

### Security and limits

- Only `http`/`https` URLs; unsafe schemes (`file:`, `ftp:`, ...) are refused before any fetch.
- **Only candidates that passed SourcePolicy are retrieved.** Rejected/prohibited
  candidates (e.g. Amazon, eBay) return a `SKIPPED` record carrying the rejection
  reason - they are never fetched.
- Hard size caps applied while streaming: HTML 5 MB, PDF 10 MB
  (`RETRIEVAL_MAX_BYTES` / `RETRIEVAL_MAX_PDF_BYTES`).
- Connection/read timeout (default 20 s, `RETRIEVAL_TIMEOUT_SECONDS`).
- No broad crawling: retrieval is per-candidate, one URL at a time; no discovery here.
- Errors are never swallowed: every failure becomes a typed `RetrievalError` and lands
  in the record's `error_kind` / `error_message`.

### Current limitations

- No JavaScript rendering (single-page app pages may yield little text).
- No OCR/vision: scanned/image-only PDFs are reported as extraction failures.
- No robots.txt / caching / rate limiting yet.
- Retrieval is one-shot (no retries).
- Real network retrieval will only ever happen for approved candidates - the tests
  are fully offline via `httpx.MockTransport` and in-memory PDF fixtures.

## Real search discovery provider (Step 6B, `backend/app/sources/providers/`)

One real provider behind the existing `SourceProvider` abstraction - no second
competing interface. Discovery and retrieval stay separate: this provider
only produces candidates; evidence retrieval is untouched.

### Search provider abstraction

```
ProductIdentity (manufacturer, brand, MPN, raw description)
      │  query builder (exact MPN quoted and preferred; raw description
      │  tokens only contribute when no MPN exists)
      v
SearchProvider (SourceProvider implementation, DiscoveryMethod.SEARCH)
      │  SearchApiClient (Serper-style JSON API; httpx; typed errors)
      v
raw search results ──► SourceCandidate (status PENDING, never pre-trusted)
      │
      v
SourcePolicy (the ONLY acceptability decision: marketplaces -> PROHIBITED,
      │            unknown external domains -> REJECTED,
      │            manufacturer-owned / allowlisted + permitted type -> ALLOWED)
      v
deterministic ranking (rank_candidates; exact MPN presence boosts score)
      v
approved candidates -> existing evidence retrieval (later stages)
```

### Configuration (backend environment only - never through React)

| Env var | Purpose |
|---|---|
| `DISCOVERY_PROVIDER` | `""` = registered registry only (default; the app starts with **no** search provider), `"search"` = the search provider. Unknown names raise `ProviderConfigurationError` lazily at discovery time. |
| `SEARCH_PROVIDER_API_KEY` | Serper-style API key; required when `DISCOVERY_PROVIDER=search` (missing key -> typed `ProviderConfigurationError`, app still starts). |
| `SEARCH_PROVIDER_BASE_URL` | Custom endpoint (default `https://google.serper.dev`); allows a local mock during integration testing. |
| `SEARCH_PROVIDER_TIMEOUT_SECONDS` | Request timeout (default 15 s). |
| `SEARCH_PROVIDER_RESULTS_LIMIT` | Max organic results per query (default 10). |

`run_discovery()` uses the `DISCOVERY_PROVIDER` selection when called with
`providers=None`; explicit `providers=[...]` (as the tests do) is unchanged.

### Why search results are never trusted automatically

- The provider emits every candidate with `status=PENDING` and no
  pre-filtering for acceptability. Only `SourcePolicy.evaluate()` can grant
  `ALLOWED`, and only for manufacturer-owned or explicitly allowlisted
  domains with a permitted source type. Amazon/eBay/AliExpress/Alibaba are
  `PROHIBITED` by the built-in marketplace labels; anything else unknown is
  `REJECTED` with a recorded reason.
- Source-type classification from a search result is conservative and
  provisional (`.pdf` -> technical PDF/manual, else product page) - the
  policy still re-checks the type against its permitted set.
- Relevance comes from the deterministic ranker (`rank_candidates`), which
  boosts exact MPN presence but can never override the policy.
- Every failure mode is typed and recorded, never fabricated: missing
  configuration (`ProviderConfigurationError`), timeout/outage/HTTP errors
  (`ProviderUnavailableError`), malformed responses
  (`ProviderInvalidResponseError`). `run_discovery()` captures these in
  `DiscoveryResult.provider_errors` and continues with the remaining
  providers. No results -> empty result, no error.

### How manufacturer-only sourcing is enforced

1. The policy only allows domains that match `manufacturer_domains` (the
   official UniHack manufacturer registry, passed via `DiscoveryContext` -
   nothing is hard-coded) or `SOURCE_ALLOWED_DOMAINS` (explicitly trusted
   external domains such as official distributors).
2. Search results from unknown domains are REJECTED, however relevant they
   look - "relevant" is never an acceptability signal.
3. Non-`http(s)` result URLs (which could never be retrieved) are skipped by
   the provider as a transport-safety filter; everything else still goes
   through the policy untouched.

### Manual integration check (real provider)

Not part of the test suite; requires backend env vars set:

```powershell
.\.venv\Scripts\python.exe -m app.sources.providers.manual_check ^
    --mpn DCB518ASTS06G --manufacturer "Freud Inc" ^
    --description "1/2 inch x 18 inch Sanding Belt 6 pack" ^
    --manufacturer-domains "freudtools.com"
```

Prints the discovered count, any typed provider errors, all allowed
candidates ranked best-first, and every rejected/prohibited candidate with
its policy reason. `SEARCH_PROVIDER_BASE_URL` can point at a local mock to
exercise the flow without a paid API.

### Tests

`backend/tests/test_search_provider.py` (35 tests) covers the query builder,
client parsing, and every failure mode via `httpx.MockTransport` canned
responses - manufacturer result, marketplace result, irrelevant result,
exact MPN match, no results, malformed responses, timeout, connection error,
missing configuration, provider selection, and end-to-end policy+ranking.
**Zero network calls: the real provider is never executed in tests.**

## UniHack dataset integration (Step 6A, `backend/app/unihack/`)

Bridges the generic internal model to the REAL official UniHack files, which
live at the repository root and are never moved:

- `Unihack_ Sample Dataset - Input.csv` — 1,000 rows × 6 columns
  (`Mfg_Part_Num, Part_Desc, E1_Brand, Unilog_Brand, DIB_Brand, Part_Manuf`).
- `Unihack_ Expected Output - Delivery Format.csv` — the official 252-column
  delivery format (2 example rows).

### Data flow

```
Unihack_ Sample Dataset - Input.csv
        │  UniHackInputParser (UTF-8 BOM, stdlib csv, raw bytes preserved)
        v
UniHackInputResult (1,000 rows · 4 placeholder tokens · MPN duplicates)
        │  to_identity() / to_product_intelligence()
        v
ProductIntelligence (generic internal model; future enrichment stages fill it)
        │  UniHackDeliveryMapper (always 252 cells, blanks instead of guesses)
        v
DeliveryRow (252 values + per-column mapping notes)
        │  DeliveryCsvWriter (UTF-8 BOM, CRLF, stdlib quoting)
        v
delivery CSV  (header loaded from the official reference file, never typed)
```

### Modules

| Module | Role |
|---|---|
| `paths.py` | repo-root resolution → the two official CSV files |
| `parser.py` | `UniHackInputParser`: exact 6-column/header/encoding checks (fatal `UniHackInputError`), row-level errors for missing `Mfg_Part_Num`/`Part_Desc` or wrong widths (rows are never dropped), the four official placeholder tokens (`-- Unbranded --`, `-- No Unilog Brand --`, `-- No DIB Brand --`, `-`) exposed semantically as missing, duplicate-MPN groups (`AVM6EV`) detected with rows kept |
| `schema.py` | `DeliverySchema.from_reference_csv()` — validates exactly 252 unique non-blank headers; `index_of()`, attribute-slot helpers; constants only for the ~50 columns the mapper touches |
| `mapper.py` | `UniHackDeliveryMapper.map(product, input_row=None) → DeliveryRow` |
| `writer.py` | `DeliveryCsvWriter` — streaming, UTF-8 with BOM, header byte-identical to the schema, row width enforced |
| `models.py` | `UniHackInputRow`/`UniHackInputResult`/`UniHackRowError`/`DeliveryRow` |

### Mapper rules

- **Verbatim passthrough** of the six input columns (placeholders included)
  from the originating input row; fallback to `ProductIdentity` otherwise.
- **Attribute triples** (`ATTRIBUTE_LABEL/VALUE/UOM 1..50`, columns 56–205):
  insertion order, normalized value preferred over raw, UOM only when known,
  first 50 slots with a truncation note beyond.
- **URLs/assets only from recorded evidence** — `MFR URL` = the
  `MANUFACTURER_PRODUCT_PAGE` evidence (else the first by id), `Ref URL 1–5`
  = remaining evidence in id order; digital assets map to columns 225–249
  via `Evidence.assets` (delivery column name → URL).
- **Never invented**: ambiguous columns (`PART_NUMBER`, `SKU -
  MY_PART_NUMBER`, `MANUFACTURER_NAME`, `BRAND_NAME`, `Dept`/`Class`/`Fine`/
  `Classpath`, codes, dimensions, pricing, warranty, country of origin, ...)
  are filled only from verified internal values and otherwise left blank with
  a "requires enrichment/verification" note on the `DeliveryRow`.
- Descriptions map to `MOBILE_DESC`/`INVOICE_DESC`/`SHORT_DESC`/`LONG_DESC1`/
  `RETAIL_DESC`/`MARKETING_DESCRIPTION`/`ITEM_FEATURES_1..20`/`With`/
  `Application`/`Includes`/`Product Name` from the extended `Descriptions`
  domain model (the new fields default to empty and stay empty until the
  future description stage fills them).

### Tests

`backend/tests/test_unihack_input.py` and `test_unihack_delivery.py` use the
ACTUAL official files (the sanctioned exception to the generic-fixtures rule):
1,000 rows, 6 columns, placeholder counts, `AVM6EV` duplicate group, 252
unique headers in exact reference order, mapper behavior, writer round-trip,
UTF-8 BOM, and `®`-byte survival. Stdlib only (`csv`); zero network calls.

## Single-product enrichment pipeline (Step 6D, `backend/app/pipeline/enrichment.py`)

The first end-to-end pipeline: one real UniHack product row flows through every
existing component and comes out as a 252-column delivery row.

```
EnrichmentRequest (6 official input fields)
   -> UniHackInputParser (placeholder semantics, raw preservation)
   -> ProductIdentity
   -> run_discovery()      (providers -> SourcePolicy -> ranking)
   -> retrieve_candidate() (HTML/PDF fetchers, limits, security gates)
   -> ExtractionService    (LLM, evidence-bound attributes only)
   -> ValidationService    (structure/evidence/normalization; official
                            LOV/UOM/manufacturer checks when AVAILABLE)
   -> ProductIntelligence  (identity + attributes + evidence + quality)
   -> UniHackDeliveryMapper (252 columns, official header order)
```

- **Typed request/result models** (`EnrichmentRequest`, `EnrichmentResult`,
  `StageState`, `InputRowView`, `DeliveryRowView`): every stage carries a
  `StageStatus` (`pending|running|completed|failed|skipped`), and the result
  keeps the discovery outcome, rejected candidates with reasons, provider
  errors, evidence records, the extraction response, the validation summary,
  the ProductIntelligence aggregate, review reasons, and derived quality
  metrics (`QualityScore`; `overall` stays 0.0 - the official formula is not
  available, nothing is fabricated).
- **The run never raises for pipeline failures.** Missing LLM configuration,
  provider outages, malformed model output, failed fetches, empty discovery,
  and rejected candidates surface as FAILED/SKIPPED stages plus
  `review_reasons`/`processing.errors` - the result is always reviewable.
  `processing.status` is `failed` (a stage failed) / `needs_review` (stages
  skipped or evidence incomplete) / `completed`.
- **No fabricated values:** with no evidence, ambiguous delivery cells stay
  blank with per-column notes; the six input columns pass through verbatim
  (placeholders included); the generated CSV is written UTF-8 with BOM and is
  never written over the official reference file.
- **Dependency injection:** providers, retriever/fetchers/transport/limits,
  LLM client, validation service, and delivery schema/mapper are all
  injectable, so the whole pipeline is tested offline (see
  `backend/tests/test_enrichment.py`).

### REST endpoint

```
POST /api/enrich
{ "Mfg_Part_Num": "DCB518ASTS06G", "Part_Desc": "...", "E1_Brand": "...",
  "Unilog_Brand": "...", "DIB_Brand": "...", "Part_Manuf": "..." }
```

Returns the full `EnrichmentResult` (delivery values + official headers,
stage statuses, review reasons, ...). At least one field must be non-blank.
The endpoint runs the REAL providers configured in the environment when it is
actually called; tests override the service dependency to stay offline.

### Manual single-product command (real providers)

```
python -m app.pipeline.manual_enrich --Mfg_Part_Num "DCB518ASTS06G" ^
  "--Part_Desc=DCB518ASTS06G Diablo 1/2\"x18\" - Sanding Belt 6pc" ^
  "--E1_Brand=-- Unbranded --" "--Unilog_Brand=-- No Unilog Brand --" ^
  "--DIB_Brand=-- No DIB Brand --" "--Part_Manuf=Freud Inc (2435)" ^
  --manufacturer-domain "freudtools.com"
```

Runs discovery/retrieval/LLM with the real settings and writes the 252-column
row to `data/delivery/<MPN>.csv` (never to the official reference file).
Real dataset row 1 (`DCB518ASTS06G`, `Part_Manuf "Freud Inc (2435)"`) is the
canonical test product. Without any provider configured, the run completes
with the chain SKIPPED and review reasons - it never crashes. Secrets stay
in `.env` and are never printed.

### Tests

`backend/tests/test_enrichment.py` (21 tests, fully offline): full happy path,
stage transitions in order, validation staying NOT_VALIDATED (never false
VERIFIED), honest quality metrics, verbatim input preservation, CSV output
with the official BOM + header and reference-file refusal, empty discovery,
all-rejected, provider error, full/partial retrieval failure, LLM provider
failure, malformed LLM output, missing LLM config, dangling evidence
references, no-LLM-without-evidence, no fabricated values, and the API
endpoint (200 + 422). Fakes only; zero network calls.

## Current limitations (deliberate)

- No UniHack Excel datasets are available in this repository; none are fabricated.
- No official LOV values, UOM rules, manufacturer/brand master data, or
  quality-gate definitions yet. The validation framework ships with UNAVAILABLE
  providers (`vocab.py` containers stay empty on purpose): nothing is ever
  reported as officially verified without the real resources.
- The real DeepSeek adapter (Step 6C) exists and is wired into the enrichment
  pipeline (Step 6D), but is never exercised live in tests: the suite uses
  fakes/mocks, so no real LLM API is ever called. The live pipeline runs only
  via the manual command / API with real credentials configured.
- Source discovery: policy + ranking + registry in place, and (Step 6B) one real
  search provider behind `SourceProvider` - it runs when explicitly
  selected via `DISCOVERY_PROVIDER=search` with a key, and the Step 6D
  pipeline wires discovery -> retrieval. Direct-URL/document providers are not
  implemented.
- Evidence retrieval foundation is in place (HTML + PDF fetchers), and evidence
  extraction (`backend/app/extraction/`) is built on top of it; the Step 6D
  pipeline wires candidates → retrieval → AI extraction → normalization/
  validation → delivery. Description generation is not wired yet; retrieval
  only ever runs on approved candidates.
- The normalization/validation framework (`backend/app/validation/`) is in place
  but only generic normalization is active; official LOV/UOM/manufacturer-brand
  providers are UNAVAILABLE by design until the official resources arrive.
- No product lookup, batch upload, dashboard, or download endpoints exist yet.
- Step 6A wires the real input CSV → internal model → 252-column delivery
  format, but no AI/discovery/enrichment runs: descriptions, brands,
  manufacturer names, taxonomy, codes, dimensions, assets, and all other
  unverified fields map as BLANK cells with per-column notes - nothing is
  invented, and the delivery example rows are NOT treated as ground truth.

## What's next (in priority order)

1. Define the domain model against the real UniHack dataset once available.
   (Step 6A done: input parsing + 252-column delivery mapping + delivery CSV
   writer against the real files; enrichment stages still to come.)
2. Implement a real LLM provider adapter (e.g. DeepSeek) behind `LLMClient._complete()`.
   (Step 6C done: the real DeepSeek adapter, offline-tested; Step 6D wires it
   into the single-product enrichment pipeline.)
3. Implement discovery providers (search / direct-URL / document) behind `sources/discovery.py`.
   (Step 6B done: the real search provider; direct-URL and document providers
   still to come.)
4. Implement pipeline stages: discovery → retrieval (`retrieve_candidate`) →
   extraction (`ExtractionService.extract`) → validation
   (`ValidationService.validate`) → description.
   (Step 6D done: the single-product pipeline covers everything except
   description generation; batch processing is still to come.)
5. Load the official UniHack resources when they arrive and implement provider
   adapters (LOV / UOM / manufacturer-brand) behind the existing interfaces.
6. Wire REST endpoints for lookup, batch, dashboard, and downloads.
6. Build the frontend upload/dashboard/review UI on top of the API.
