# Product Truth Engine

[![tests](https://img.shields.io/badge/tests-offline-brightgreen)](https://github.com/prabhas665/unihack-product-truth-engine)
[![license](https://img.shields.io/badge/license-MIT-blue)](LICENSE)

TL;DR: An evidence-first product-enrichment pipeline that turns limited or messy product identity rows (manufacturer, brand, MPN, description) into an auditable, 252-column UniHack delivery CSV. Built for correctness and auditability: every emitted attribute must cite source evidence and nothing is fabricated.

Status: Working end-to-end locally with extensive offline tests. The repository includes a Render blueprint for deployment and a submission/ folder with demo materials.

Contents
- Quick Start
- Usage
- Configuration
- Architecture (summary)
- Tests
- Deployment
- Contributing

---

## Quick Start

Prereqs: Python 3.11, node (for frontend), npm.

Clone and create a virtual environment:

```bash
git clone https://github.com/prabhas665/unihack-product-truth-engine.git
cd unihack-product-truth-engine
python -m venv .venv
# macOS / Linux
source .venv/bin/activate
# Windows PowerShell
# .\.venv\Scripts\Activate.ps1

pip install -r backend/requirements.txt
```

Run the backend (development):

```bash
uvicorn app.main:app --reload --port 8000
# health check
curl http://127.0.0.1:8000/api/health
```

Run the frontend (dev):

```bash
cd frontend
npm install
npm run dev
# open http://127.0.0.1:5173
```

Notes:
- For local tests and development use the built-in `fake` LLM provider: `LLM_PROVIDER=fake` (no external API keys required).
- Runtime data (SQLite DB and generated CSVs) lives in `data/` (gitignored).

---

## Usage

Single-product enrichment (example):

```bash
curl -s -X POST http://127.0.0.1:8000/api/enrich \
  -H "Content-Type: application/json" \
  -d '{
    "Mfg_Part_Num": "DCB518ASTS06G",
    "Part_Desc": "1/2 inch x 18 inch Sanding Belt 6 pack",
    "Part_Manuf": "Freud Inc"
  }'
```

Response: the full `EnrichmentResult` JSON containing stage states, evidence records, extracted attributes, validation summaries and a 252-cell delivery row view. See `POST /api/enrich` in `backend/app/api/routes/enrich.py` for details.

Lookup an input row from the official UniHack sample dataset:

```bash
curl "http://127.0.0.1:8000/api/lookup?mpn=DCB518ASTS06G"
```

Batch runs and downloads are available via `POST /api/batch` and `GET /api/downloads/{name}` (see API routes).

---

## Configuration

A comprehensive template is available in `.env.example`. Short summary of important env vars:

- `LLM_PROVIDER` — adapter name (`fake` for tests, `deepseek` etc.).
- `LLM_API_KEY`, `LLM_MODEL`, `LLM_BASE_URL`, `LLM_TIMEOUT_SECONDS` — provider configuration.
- `DISCOVERY_PROVIDER`, `SEARCH_PROVIDER_API_KEY`, `SEARCH_PROVIDER_BASE_URL` — discovery/search provider settings.
- `DATABASE_URL` / `DATA_DIR` — data persistence location (defaults in `.env.example`).
- `BATCH_MAX_ROWS`, `BATCH_PAYLOAD_EVIDENCE_CAP_CHARS` — batch safeguards.

Defaults and detailed descriptions live in `.env.example` in the repository root.

Security note: API keys must only be set in the environment; they are never committed, never returned in API responses, and not logged.

---

## Architecture (brief)

High level flow: EnrichmentRequest → discovery (source candidates) → retrieval (HTML/PDF fetchers) → evidence-based extraction (LLM via provider adapter) → validation & normalization → UniHack delivery mapper → optional persistence.

Key modules (backend/app):
- api/ — REST endpoints (enrich, lookup, batch, dashboard, downloads, health)
- pipeline/ — orchestration for staged enrichment
- sources/ — discovery, policy, ranking and retrieval (HTML/PDF)
- extraction/ — evidence-bound extraction service + prompt builder
- llm/ — provider-agnostic LLM client + adapters (fake, deepseek)
- validation/ — normalization and validation framework (LOV/UOM providers are intentionally UNAVAILABLE until official data is provided)
- unihack/ — UniHack CSV parser, 252-column delivery schema, mapper
- db/ — SQLAlchemy models + ProductRepository persistence

The project favors dependency injection and defensive guards (no fabrications, typed errors, and strict evidence requirements).

For a long-form design and module map see `submission/` and `AUDIT.md` in the repo.

---

## Tests

Run backend tests (offline — no network):

```bash
# from repo root, with venv activated
pytest backend/tests
```

Tests use `FakeLLMClient` and `httpx.MockTransport` so the suite does not make network calls. Key test files live under `backend/tests/` (enrichment, extraction, unihack input/delivery, sources, descriptions, API extras).

---

## Deployment

A `render.yaml` blueprint is included for single-service Render deployments (committed frontend dist + FastAPI). Deployment notes and recommended production env vars are in the original README and `.env.example`.

Quick checklist for live runs:
- Set search/LLM provider keys (`SEARCH_PROVIDER_API_KEY`, `GEMINI_API_KEY`/`LLM_API_KEY` or equivalent).
- Set `DATABASE_URL` or `DATA_DIR` to a persistent disk.
- Set `EVALUATION_API_TOKEN` to gate paywalled evaluation endpoints.

---

## Contributing

Contributions and bug reports are welcome. Open an issue describing the problem and include logs or test output when possible. For larger changes, please open a PR with tests and keep semantics (no evidence fabrication).

Suggested first contributions:
- Add a GitHub Actions workflow to run tests.
- Provide a Dockerfile / docker-compose for easy local runs.
- Shorten the top-level README by moving deep design notes into `docs/` and create a concise Quick Start.

---

## License

This project is provided under the MIT license (see LICENSE file).

---

If you want, I can:
- split the long README into a brief top-level README + docs/ARCHITECTURE.md and docs/DEVELOPMENT.md and open a PR with those changes, or
- add a GitHub Actions workflow and Dockerfile in a follow-up PR.

Which would you like me to do next?