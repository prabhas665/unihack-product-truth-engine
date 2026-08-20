"""Discovery provider package (Step 6B).

Holds provider implementations behind the existing SourceProvider protocol
(app/sources/discovery.py) and the environment-driven selection used by
run_discovery() by default:

- DISCOVERY_PROVIDER unset        -> the registered provider registry is used
  (backward compatible; no search provider registered by default, so the
  application starts without any search configuration).
- DISCOVERY_PROVIDER=search       -> the search provider, configured entirely
  from backend environment variables (SEARCH_PROVIDER_*).
- DISCOVERY_PROVIDER=gemini       -> the Gemini search discovery provider,
  configured entirely from backend environment variables (GEMINI_*).
- DISCOVERY_PROVIDER=groq         -> the Groq Web Search discovery provider
  (discovery ONLY; the LLM layer stays on OpenRouter). Configured entirely
  from backend environment variables (GROQ_*).
- DISCOVERY_PROVIDER=search,gemini -> comma-separated list: Serper primary,
  Gemini automatic backup if Serper returns nothing allowed.
- unknown name                    -> ProviderConfigurationError at discovery
  time (lazy; the application itself never fails to start).
"""

from __future__ import annotations

from app.config import settings
from app.sources.discovery import PROVIDERS, SourceProvider
from app.sources.errors import ProviderConfigurationError
from app.sources.providers.gemini_search import GeminiSearchProvider
from app.sources.providers.groq_search import GroqSearchProvider
from app.sources.providers.search import (
    DEFAULT_BASE_URL,
    SearchApiClient,
    SearchProvider,
    SearchResult,
    build_recall_query,
    build_search_query,
    guess_source_type,
)

__all__ = [
    "DEFAULT_BASE_URL",
    "SearchApiClient",
    "SearchProvider",
    "SearchResult",
    "build_recall_query",
    "build_search_query",
    "guess_source_type",
    "providers_from_settings",
]

_SUPPORTED_NAMES: dict[str, type[SourceProvider]] = {
    SearchProvider.name: SearchProvider,  # "search" -> Serper
    "gemini": GeminiSearchProvider,
    "groq": GroqSearchProvider,
}


def _build_provider(name: str) -> SourceProvider:
    """Build a single provider by name; raises on unknown."""
    cls = _SUPPORTED_NAMES.get(name)
    if cls is None:
        raise ProviderConfigurationError(
            name,
            f"unknown discovery provider {name!r}; supported names: "
            f"{', '.join(repr(n) for n in _SUPPORTED_NAMES)}",
        )
    return cls.from_settings(settings)


def providers_from_settings() -> list[SourceProvider]:
    """Providers to use for a default discovery run, per DISCOVERY_PROVIDER.

    Accepts a single name or a comma-separated list (e.g. ``search,gemini``).
    Whitespace and empty items are stripped; duplicates are removed while
    preserving order.
    """
    raw = (settings.discovery_provider or "").strip().lower()
    if not raw:
        return list(PROVIDERS)

    seen: set[str] = set()
    providers: list[SourceProvider] = []
    for token in raw.split(","):
        name = token.strip()
        if not name or name in seen:
            continue
        seen.add(name)
        providers.append(_build_provider(name))
    if providers:
        return providers

    raise ProviderConfigurationError(
        raw,
        f"unknown discovery provider {raw!r}; supported names: "
        f"{', '.join(repr(n) for n in _SUPPORTED_NAMES)}",
    )
