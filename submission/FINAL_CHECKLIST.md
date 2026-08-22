# Final Submission Checklist

All items verified on 2026-08-22 (local machine, Windows / Python 3.11.9).

| # | Item | Status | Evidence |
|---|---|---|---|
| 1 | Backend tests pass | **PASS** | `tests/test_bootstrap.py` 40/40 passed; `tests/test_enrichment.py` 35/35 passed (incl. all 6 bootstrap-integration tests); happy-path suite 7/7 |
| 2 | Frontend tests pass | **PASS** | `npm test` → vitest: 2/2 passed (identity-safety P0 regressions) |
| 3 | Frontend builds | **PASS** | `npm run build` → tsc + vite: ✓ built in ~1.8s (169 kB JS gzipped 53.7 kB) |
| 4 | GitHub up to date | **SEE NOTE** | Local branch ahead of remote: submission docs, README refresh, frontend evaluation-refresh fix, demo script are uncommitted — commit & push before deadline |
| 5 | Render deployment status | **LIVE** | `GET https://unihack-product-truth-engine.onrender.com/api/health` → `{"status":"ok","app":"Product Truth Engine","version":"0.1.0"}` |
| 6 | No secrets committed | **PASS** | `git ls-files` contains only `.env.example`; real `backend/.env` confirmed gitignored; no key material tracked |
| 7 | Demo MPN #1 (`49-94-0013`) works | **PASS** | Live run: registry identity (Milwaukee Tool / Milwaukee, provenance=mpn), 5+ attributes @ confidence 1.00, full description set, retailers rejected with reasons |
| 8 | Demo MPN #2 (`DCB518ASTS06G`) works | **PASS** | Live run: needs_review path exercised — marketplace/retailer candidates rejected, verified identity left blank, attributes still enriched from diablotools.com (the one trusted page) |
| 9 | CSV download works | **PASS** | Batch POST `/api/batch` → 200; GET `/api/downloads/{name}` → 200, `text/csv`, downloaded file has exactly **252 columns** |
| 10 | Evaluation endpoint guarded + working | **PASS** | `EVALUATION_API_TOKEN` present in `backend/.env` (12 chars, no whitespace); offline evaluation verified in prior session (8/8 API tests) |

**Note on item 4:** the only outstanding action is a final `git add` / `git commit` / `git push` of documentation and the previously verified frontend fix. No production backend code changed during submission prep.

## Pre-demo sanity routine (recommended)
1. Hit `/api/health` on Render (free tier cold-start can take ~30–60 s — warm it up before judges arrive).
2. Run `49-94-0013` once to warm discovery/extraction caches.
3. Keep this checklist and `submission/JUDGE_TALKING_POINTS.md` open in a second window.
