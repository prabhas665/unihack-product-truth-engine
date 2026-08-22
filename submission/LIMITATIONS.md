# Known Limitations

## Official UniHack Resources Unavailable
- **LOV/UOM Lists**: The official Lists of Values (LOV) and Units of Measure (UOM) prescribed by the UniHack challenge were not provided in the submission environment. Consequently, validation coverage may be lower than possible, and attributes requiring those lists remain blank instead of being normalized.
- **Taxonomy/Classification**: The official UniHack product taxonomy (Dept/Class/Fine/Classpath/etc.) is unavailable. The pipeline leaves those columns blank rather than inventing classifications.
- **Impact**: This is a known constraint of the hackathon environment, not a bug. The system correctly avoids fabrication by leaving fields empty when authoritative resources are missing.

## Free‑Tier Provider Quotas
- **Gemini API**: Rate‑limited on the free tier; we mitigate with 5‑key rotation, but bursts of >5 requests/minute may trigger 429 errors.
- **Groq/NVIDIA**: Free tiers have token‑per‑minute limits; prolonged batch jobs may encounter temporary throttling.
- **Discovery**: DuckDuckGo via `ddgs` is free but may impose usage limits under heavy load.
- **Mitigation**: The provider failover chain and retry logic reduce the chance of total failure; batch jobs automatically back off and resume.

## No Fabrication of Missing Fields
- When evidence does not contain a value for a requested attribute (e.g., torque for a product that is not a motor), the attribute is left blank.
- This means some rows may have fewer enriched columns than theoretically possible, but it guarantees correctness.
- Users should interpret blank fields as “unknown from trusted sources” rather than “not applicable”.

## Dependency on Internet Connectivity
- The enrichment pipeline requires outbound HTTPS to discover and retrieve manufacturer sources.
- In fully air‑gapped environments, the system would need to operate in offline mode with pre‑cached evidence (supported by the evaluation harness but not the default web mode).

## Bootstrap Identity Scope
- Bootstrapped manufacturer identity is trusted only for the current enrichment run; it is **not** written back to `verified_brands.json`. This preserves the trust controls but means the same MPN must re‑run bootstrap on each enrichment (caching could be added but is omitted to avoid side‑effects).

## Evidence Text Length
- Retrieval extracts plain text from HTML but truncates to a configurable limit (default large enough for typical product pages). Extremely long pages may have tail content omitted, though the MPN and key attributes are usually near the top.

## JavaScript‑Rendered Content
- The retrieval fetches static HTML; it does not execute JavaScript. If a manufacturer relies on client‑side rendering to display the MPN or attributes, those sources may appear as missing text. In practice, most industrial product sites serve server‑rendered HTML or provide the MPN in the raw HTML.

## Single‑Product Focus
- The core enrichment service processes one product at a time. Batch processing is orchestrated externally but still runs individual enrichments sequentially (with concurrency limited by provider rate limits).

---