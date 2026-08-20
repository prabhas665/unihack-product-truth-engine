from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    app_name: str = "Product Truth Engine"
    version: str = "0.1.0"

    # LLM layer: provider name must match a registered adapter in llm/providers/.
    # No API key is required for the application to start; clients are built
    # lazily by llm.get_client().
    llm_provider: str = ""
    llm_api_key: str = ""
    # Optional provider model override; empty = provider default.
    llm_model: str = ""
    # Optional provider base URL override (e.g. a local mock or proxy);
    # empty = provider default endpoint.
    llm_base_url: str = ""
    # Default timeout for provider calls (seconds); per-request overrides exist.
    llm_timeout_seconds: float = 30.0
    # Optional fallback models for EXTRACTION ONLY (Step LLM-8): used only
    # when the primary LLM call times out or is unavailable, tried in
    # order. Empty = that fallback is skipped (current behavior when both
    # are empty). Each fallback reuses the primary provider configuration
    # (same key and base URL) with a different model id, unless
    # LLM_FALLBACK_PROVIDER (or _2) names a different registered provider
    # (e.g. "nvidia"): that provider's own key/base URL are then used, so a
    # mixed chain like "gemini primary -> nvidia fallback" is possible.
    llm_fallback_model: str = ""
    # Provider name for the first fallback; empty = the primary provider.
    llm_fallback_provider: str = ""
    # Per-attempt timeout for the first fallback model; None = reuse
    # LLM_TIMEOUT_SECONDS.
    llm_fallback_timeout_seconds: float | None = None
    # Second ordered fallback model for extraction (attempted only when
    # the primary AND the first fallback both time out or are unavailable).
    llm_fallback_model_2: str = ""
    # Provider name for the second fallback; empty = the primary provider.
    llm_fallback_provider_2: str = ""
    # Per-attempt timeout for the second fallback model; None = reuse
    # LLM_FALLBACK_TIMEOUT_SECONDS, then LLM_TIMEOUT_SECONDS.
    llm_fallback_timeout_seconds_2: float | None = None

    # Transient-failure retries with exponential backoff. A provider call
    # that fails with a retryable error (rate limit, transient network) is
    # retried up to this many ADDITIONAL times on the same provider before
    # the failover chain is consulted. Base delay doubles per attempt.
    llm_retry_attempts: int = 2
    retry_base_delay_seconds: float = 1.0
    # Additional attempts for a single discovery provider call (same policy).
    discovery_retry_attempts: int = 1

    # Hard wall-clock deadline for ONE pipeline run (seconds). When the
    # deadline passes, LLM-bound stages (extraction/descriptions) are skipped
    # with a NEEDS_REVIEW reason instead of starting a fresh paid call; the
    # run still returns with the identity, evidence and delivery preserved.
    pipeline_run_deadline_seconds: int = 180

    # Source discovery policy: comma-separated domain patterns (see
    # app/sources/policy.py). No UniHack data is hard-coded here; the
    # official manufacturer domain registry fills in later.
    source_allowed_domains: str = ""
    source_prohibited_domains: str = ""

    # Discovery provider selection (Step 6B): name of the provider to use
    # when run_discovery() is called without explicit providers. "" = use the
    # registered provider registry (no search provider by default, so the
    # application starts without any search configuration). Supported names:
    # "search".
    discovery_provider: str = ""
    # Search provider (Serper-style JSON API): the API key must only ever be
    # set in backend environment variables - never in the frontend.
    search_provider_api_key: str = ""
    # Custom endpoint for the search API (allows a local mock in integration
    # testing). Defaults to the public Serper endpoint.
    search_provider_base_url: str = "https://google.serper.dev"
    search_provider_timeout_seconds: float = 15.0
    # Max organic results requested per query.
    search_provider_results_limit: int = 10

    # --- Gemini search discovery (Step 11) ---
    # Configuration for the Gemini Search Discovery provider.
    # The application starts without any of these set; a missing GEMINI_API_KEY
    # surfaces as a ProviderConfigurationError at discovery time, never at startup.
    GEMINI_API_KEY: str = ""
    # Comma-separated list of Gemini API keys used to rotate around the
    # per-key rate limit (GEMINI_API_KEY is the first/primary key). Each key
    # has its own free-tier quota, so N keys multiply throughput by up to N.
    GEMINI_API_KEYS: str = ""
    # Gemini model name; "gemini-flash-latest" is the stable alias for
    # grounding-capable models. Set GEMINI_MODEL in backend/.env to override.
    GEMINI_MODEL: str = "gemini-flash-latest"
    # Base URL for the Gemini generateContent endpoint.
    GEMINI_BASE_URL: str = "https://generativelanguage.googleapis.com"
    # Request timeout in seconds
    GEMINI_TIMEOUT_SECONDS: float = 20.0
    # Maximum number of grounding results to surface
    GEMINI_RESULTS_LIMIT: int = 10

    # --- Groq web search discovery (Step 12B) ---
    # Configuration for the Groq Web Search discovery provider. Discovery ONLY;
    # this never touches the LLM layer (LLM_PROVIDER stays openrouter). The
    # application starts without any of these set; a missing GROQ_API_KEY
    # surfaces as a ProviderConfigurationError at discovery time, never at
    # startup. GROQ_MODEL_VERSION (basic-search pricing) is pinned in code.
    GROQ_API_KEY: str = ""
    # Groq model with web_search support; "groq/compound-mini" is the
    # cost-efficient variant. Set GROQ_MODEL in backend/.env to override.
    GROQ_MODEL: str = "groq/compound-mini"
    # Base URL for the OpenAI-compatible chat/completions endpoint.
    GROQ_BASE_URL: str = "https://api.groq.com"
    # Request timeout in seconds
    GROQ_TIMEOUT_SECONDS: float = 20.0
    # Maximum number of web_search results to surface per query
    GROQ_RESULTS_LIMIT: int = 10

    # --- NVIDIA NIM LLM provider (LLM_PROVIDER=nvidia) ---
    # OpenAI-compatible chat/completions API (build.nvidia.com). Set
    # LLM_PROVIDER=nvidia to route extraction/description LLM calls through
    # NVIDIA NIM. A missing NVIDIA_NIM_API_KEY surfaces as a typed
    # LLMConfigurationError at call time, never at startup.
    NVIDIA_NIM_API_KEY: str = ""
    # Model id served by the NIM endpoint; "nvidia/nemotron-3.5-lightning-30b-a3b"
    # is the fast free-tier default. Set NVIDIA_MODEL in backend/.env to override.
    NVIDIA_MODEL: str = "nvidia/nemotron-3.5-lightning-30b-a3b"
    # Base URL for the OpenAI-compatible chat/completions endpoint.
    NVIDIA_BASE_URL: str = "https://integrate.api.nvidia.com"
    # Request timeout in seconds
    NVIDIA_TIMEOUT_SECONDS: float = 30.0

    # Evidence retrieval limits (see app/sources/retrieval/limits.py).
    retrieval_timeout_seconds: float = 20.0
    retrieval_max_bytes: int = 5_000_000  # HTML responses
    retrieval_max_pdf_bytes: int = 25_000_000
    retrieval_user_agent: str = "ProductTruthEngine/0.1 (hackathon)"
    # Cap on how many allowed candidates are actually fetched per product;
    # keeps per-product latency bounded (pages are fetched sequentially).
    retrieval_max_candidates: int = 6
    # Cap on extracted-readable-text per evidence record (characters), applied
    # AFTER HTML/PDF text extraction so the stored evidence text stays bounded
    # even when a page/PDF yields far more text than the 20k prompt budget.
    # Raw byte caps (above) are unchanged. None disables the text cap.
    retrieval_max_text_chars: int | None = 20_000

    # Extraction evidence-selection budget (Step 20): an upper bound on the
    # total evidence characters handed to the LLM extraction call. Sibling
    # manufacturer pages that describe a DIFFERENT product (and never mention
    # the requested MPN) are excluded, and the remaining, MPN-relevant records
    # are included in priority order until this budget is reached. Keeps the
    # extraction prompt comfortably small so a slow free-tier model cannot be
    # starved by 5 full sibling pages. The per-record cap
    # (MAX_CHARS_PER_RECORD) is enforced separately inside the extraction
    # prompt builder.
    extraction_context_budget_chars: int = 12_000

    # Batch guardrails (Step 9B): hard cap on rows enriched per POST /api/batch
    # and per list of MPNs. Requests above the cap are rejected with 422, never
    # silently truncated.
    batch_max_rows: int = 50
    # Cap on the evidence text stored inside a persisted batch payload (chars
    # per evidence record). Raw retrieval output can reach megabytes per source;
    # capping the stored copy keeps SQLite growth sane while preserving
    # traceability (evidence ids, URLs and attribute quotes are untouched).
    batch_payload_evidence_cap_chars: int = 20_000

    database_url: str = "sqlite:///./data/unihack.db"
    app_host: str = "127.0.0.1"
    app_port: int = 8000

    # CORS: comma-separated list of allowed browser origins. Defaults to the
    # local Vite dev origins so development works out of the box. For a public
    # deployment set CORS_ALLOWED_ORIGINS to the production frontend origin (or
    # "*" if the frontend uses no credentials, which it does not).
    cors_allowed_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    # Optional path to a built frontend (frontend/dist) to serve same-origin via
    # the FastAPI app. Empty = do not serve static files (e.g. local dev where
    # Vite serves the frontend). Set FRONTEND_DIST_DIR for a self-contained
    # single-service deployment.
    frontend_dist_dir: str = ""

    # Optional base directory for runtime-generated data (SQLite DB, batch and
    # delivery CSVs). Empty = use the repo-root data/ directory. For deployments
    # with a persistent disk, set DATA_DIR to the mounted volume path so the
    # database and downloads survive restarts. SQLite also honors DATABASE_URL.
    data_dir: str = ""

    # Step 10B persistent product-intelligence cache. A stored record is
    # considered FRESH when ``last_enriched_at`` is within this many days;
    # older records are STALE and either flagged as such or re-enriched on
    # demand. Set to 0 to disable freshness (everything is treated as stale).
    product_cache_freshness_days: int = 30

    # Bearer token required by POST /api/evaluation/run. When unset the
    # endpoint is disabled (403): it reads arbitrary files, writes reports
    # and can trigger paid live pipeline runs, so it must never be exposed
    # without an explicit key.
    evaluation_api_token: str = ""


    @property
    def cors_origins(self) -> list[str]:
        return [o.strip() for o in self.cors_allowed_origins.split(",") if o.strip()]

    @property
    def gemini_api_keys(self) -> list[str]:
        """Ordered list of Gemini API keys for rate-limit rotation.

        GEMINI_API_KEYS (comma-separated) wins when set; otherwise the single
        GEMINI_API_KEY is used. Empty strings are dropped so a trailing comma
        never produces a bogus key.
        """
        raw = (self.GEMINI_API_KEYS or "").strip()
        if raw:
            keys = [k.strip() for k in raw.split(",")]
        else:
            keys = [self.GEMINI_API_KEY or ""]
        return [k for k in keys if k]

    def runtime_data_dir(self) -> "Path":
        from pathlib import Path

        from app.unihack.paths import repo_root

        if self.data_dir.strip():
            return Path(self.data_dir.strip())
        return repo_root() / "data"


settings = Settings()
