# Key Features

## Evidence-First Enrichment
- Every attribute value is backed by at least one evidence record (retrieved source snippet).
- Evidence includes URL, retrieval timestamp, and trust level.
- No attribute is populated without verifiable source support.

## Trusted Manufacturer Identity
- Identity bootstrap requires strong evidence: exact MPN presence, manufacturer consistency, no sibling contamination, non-marketplace.
- Bootstrap identity is treated as locally verified for the run but never written to the global seed file (preserves trust controls).

## Retailer/Distributor Rejection
- SourcePolicy rejects any candidate whose hostname is not an exact manufacturer-owned domain.
- Blocks known retailers (Ace Hardware, Home Depot, McCoys) and distributors unless they are also the manufacturer.

## Sibling-Product Isolation
- Evidence containing a different MPN from the same manufacturer is excluded as sibling contamination.
- Prevents attribute bleed‑over from related products (e.g., a sander model’s specs leaking into a sanding belt’s enrichment).

## Claim‑Support Validation
- Extracted values are checked against official lists of units (UOM) and allowed values (LOV) when available.
- Conflicts between multiple evidence sources are recorded and surfaced in review reasons.
- Validation coverage quantifies how many attributes could be normalized/validated.

## LLM Failover & Resilience
- Primary provider: Gemini (5‑key rotation for rate‑limit tolerance).
- First fallback: Groq-hosted `allam-2-7b` (free tier, different provider).
- Second fallback: NVIDIA `nemotron-3.5` (120‑second timeout).
- If all LLMs fail, extraction stage is marked FAILED but the pipeline continues to produce a NEEDS_REVIEW result.

## Groq + DuckDuckGo Discovery
- Discovery uses DuckDuckGo (via `ddgs`) as the primary provider.
- Groq‑powered Gemini serves as a fallback when DuckDuckGo is rate‑limited or unavailable.
- No reliance on paid search APIs; fully free‑tier operable.

## Guaranteed 252‑Column Output
- Regardless of enrichment success or failure, the pipeline always emits a CSV row with exactly 252 columns matching the UniHack schema.
- Missing data appears as blanks; never omits columns or invents placeholders.

## NEEDS_REVIEW as a Feature
- Stages that cannot complete (discovery, extraction, validation) are marked FAILED or SKIPPED.
- Review reasons explain *why* enrichment halted (e.g., “no trusted manufacturer source”, “LLM timeout”).
- Enables operators to trust the system’s uncertainty rather than guessing.

## Batch Processing
- `/api/batch` endpoint accepts dozens of rows, processes them in parallel chunks, and persists progress.
- Resumable via local progress JSON; survives interruptions.

## Evaluation Harness
- Offline evaluation mode (`live:false`) runs enrichment without network calls, using cached evidence.
- Used for regression testing and verifying 252‑column shape.
- Guarded by `EVALUATION_API_TOKEN` to prevent unauthorized use.