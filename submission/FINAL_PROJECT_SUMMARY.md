# Product Truth Engine - UniHack Submission

## Problem Statement
Industrial product data is often incomplete, inconsistent, or missing critical attributes needed for commerce, maintenance, and compliance. Users typically have only a manufacturer part number (MPN), brand, or manufacturer name, which is insufficient for downstream systems requiring standardized, enriched product information.

## Solution
The Product Truth Engine takes minimal product input (MPN, brand, manufacturer) and autonomously discovers trusted manufacturer sources, extracts evidence-backed attributes, validates against official vocabularies, generates standardized descriptions, and outputs a complete 252-column UniHack delivery row with full traceability—all without inventing facts.

## Key Differentiators
- **Evidence-First**: Every attribute is tied to a retrievable source; no LLM fabrication.
- **Trust-Aware**: Strict SourcePolicy rejects unknown/distributor/sibling sources; bootstrap only with strong evidence.
- **Robust Fallbacks**: Multi-LLM provider chain (Gemini → Groq → NVIDIA) and discovery providers (DuckDuckGo) handle rate limits and failures.
- **NEEDS_REVIEW**: Transparently flags incomplete enrichment instead of guessing, enabling human-in-the-loop verification.

## Technologies Used
- **Backend**: FastAPI, Python 3.11, Pydantic, SQLAlchemy (SQLite)
- **Frontend**: React, Vite, TypeScript
- **LLM Providers**: Gemini (primary), Groq/allam-2-7b (fallback), NVIDIA/nemotron-3.5 (secondary)
- **Discovery**: DuckDuckGo (with Groq fallback)
- **Deployment**: Render (free tier) with persistent disk