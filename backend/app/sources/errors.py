"""Typed errors raised by discovery providers (Step 6B).

run_discovery() catches ProviderError raised by a provider's discover() and
records it on the DiscoveryResult (provider_errors) so one broken provider
never aborts a discovery run and never fabricates results. The application
can start without any provider configured: providers are built lazily from
environment settings when discovery actually runs.
"""

from __future__ import annotations


class ProviderError(Exception):
    """Base class for all discovery-provider failures."""

    # Stable machine-readable kind; mirrored onto ProviderErrorInfo.error_kind.
    kind: str = "provider"

    def __init__(self, provider_name: str, message: str) -> None:
        self.provider_name = provider_name
        self.message = message
        super().__init__(f"{provider_name}: {message}")


class ProviderConfigurationError(ProviderError):
    """Provider cannot run: missing/invalid configuration (e.g. no API key)."""

    kind = "configuration"


class ProviderUnavailableError(ProviderError):
    """Provider is unreachable: timeout, network failure, HTTP error."""

    kind = "unavailable"


class ProviderInvalidResponseError(ProviderError):
    """Provider answered but the response could not be interpreted."""

    kind = "invalid_response"
