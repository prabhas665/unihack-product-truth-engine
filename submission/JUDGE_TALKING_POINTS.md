# Judge Talking Points

## Why This Is Better Than Blindly Asking an LLM
- **No Fabrication**: LLMs alone hallucinate specs, manufacturers, or certifications. Our system only outputs what it can verify from a retrieved source.
- **Traceability**: Every attribute points to an evidence record with a URL and timestamp; you can click and see the original source.
- **Up‑to‑Date**: Uses live web discovery (within rate limits) rather than a static model snapshot.
- **Domain‑Aware**: Knows the difference between a manufacturer site and a retailer/sibling page; an LLM prompt cannot reliably enforce that without extensive few‑shot examples and still risks error.

## How Fabrication Is Prevented
- **Evidence‑First Pipeline**: Extraction stage receives only text from trusted sources; the LLM prompt explicitly forbids adding information not present in the snippet.
- **Claim‑Support Gate**: Before an attribute value is accepted, the system checks that the evidence snippet actually contains the claimed value (or a normalizable variant). If not, the value is rejected.
- **No Guessing**: If extraction fails to find a value for a requested attribute, that attribute remains empty rather than being filled with a plausible‑sounding guess.
- **Unit/LOV Validation**: When official resources are available, values are normalized and checked; out‑of‑scope values are left blank instead of being forced.

## How Retailer/Sibling Contamination Is Prevented
- **SourcePolicy Deny‑By‑Default**: A candidate is usable only if its hostname matches an approved manufacturer domain. Retailer domains (amazon.com, acehardware.com, homedepot.com) are never approved.
- **Bootstrap Trust Rules**: To bootstrap a manufacturer identity, the system requires:
  - Exact MPN presence in the text (or URL path).
  - Manufacturer consistency (the extracted manufacturer matches the input or is verifiable from the seed).
  - No sibling contamination (the page must not mention a different MPN from the same manufacturer).
  - Non‑marketplace source.
- **Evidence Selection**: Even if a retailer page passes discovery (it won’t because of policy), the evidence selection step discards any snippet that does not contain the target MPN or that mentions a different MPN from the same manufacturer.

## How the System Survives Provider Failures
- **LLM Failover Chain**: Gemini (5‑key rotation) → Groq/allam‑2‑7b → NVIDIA nemotron‑3.5. If one provider hits rate limit or error, the next is tried automatically.
- **Discovery Provider Fallback**: DuckDuckGo primary; if it fails (e.g., 429), the system can fall back to a Groq‑powered Gemini search (configured via `DISCOVERY_PROVIDER`).
- **Graceful Degradation**: If all LLMs fail, extraction stage is marked FAILED but the pipeline continues; the result still includes passthrough input, any successfully retrieved evidence, and a clear NEEDS_REVIEW reason.
- **Retry & Backoff**: Network fetches use exponential backoff and jitter; transient errors do not crash the run.
- **Caching & Offline Mode**: For development and testing, the evaluation harness can run with cached evidence, proving the system works without any external provider.

## Why NEEDS_REVIEW Is a Feature, Not a Failure
- **Honest Uncertainty**: The system tells you exactly why it could not complete enrichment (e.g., “no trusted manufacturer source”, “LLM timeout”). This is more valuable than a wrong answer.
- **Human‑In‑The‑Loop**: Operators can decide whether to accept partial enrichment, gather additional context, or flag the item for manual research.
- **Risk Aversion**: In industrial supply chains, assigning the wrong manufacturer or spec can lead to safety issues, costly returns, or line‑stoppage. NEEDS_REVIEW prevents those risks.
- **Audit Trail**: Every NEEDS_REVIEW run leaves a reproducible record of what was attempted, what evidence was found, and why it was rejected—essential for compliance and quality assurance.

## Additional Points
- **Open‑Source & Transparent**: All trust controls are visible in the source; no hidden weights or black‑box decisions.
- **Free‑Tier Operable**: Uses only free APIs and providers; suitable for hackathon constraints and real‑world low‑budget deployments.
- **Standards‑First**: Outputs adhere to the UniHack 252‑column schema; passthrough fields guarantee no input data loss.
- **Extensible**: Adding new LLM providers, discovery sources, or validation rules is straightforward via dependency injection.

---