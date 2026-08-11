"""Discovery provider package (Step 6B).

Holds provider implementations behind the existing SourceProvider protocol
(app/sources/discovery.py) and the environment-driven selection used by
run_discovery() by default:

- DISCOVERY_PROVIDER unset        -> the registered provider registry is used
  (backward compatible; no search provider registered by default, so the
  application starts without any search configuration).
- DISCOVERY_PROVIDER=search       -> the search provider, configured entirely
  from backend environment variables (SEARCH_PROVIDER_*).
- unknown name                    -> ProviderConfigurationError at discovery
  time (lazy; the application itself never fails to start).
"""

from __future__ import annotations

from app.config import settings
from app.sources.discovery import PROVIDERS, SourceProvider
from app.sources.errors import ProviderConfigurationError
from app.sources.providers.search import (
    DEFAULT_BASE_URL,
    SearchApiClient,
    SearchProvider,
    SearchResult,
    build_search_query,
    guess_source_type,
)

__all__ = [
    "DEFAULT_BASE_URL",
    "SearchApiClient",
    "SearchProvider",
    "SearchResult",
    "build_search_query",
    "guess_source_type",
    "providers_from_settings",
]


def providers_from_settings() -> list[SourceProvider]:
    """Providers to use for a default discovery run, per DISCOVERY_PROVIDER."""
    name = (settings.discovery_provider or "").strip().lower()
    if not name:
        return list(PROVIDERS)
    if name == SearchProvider.name:
        return [SearchProvider.from_settings(settings)]
    raise ProviderConfigurationError(
        name,
        f"unknown discovery provider {name!r}; supported names: "
        f"'{SearchProvider.name}'",
    )
