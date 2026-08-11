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

    # Evidence retrieval limits (see app/sources/retrieval/limits.py).
    retrieval_timeout_seconds: float = 20.0
    retrieval_max_bytes: int = 5_000_000  # HTML responses
    retrieval_max_pdf_bytes: int = 10_000_000
    retrieval_user_agent: str = "ProductTruthEngine/0.1 (hackathon)"

    database_url: str = "sqlite:///./data/unihack.db"
    app_host: str = "127.0.0.1"
    app_port: int = 8000


settings = Settings()
