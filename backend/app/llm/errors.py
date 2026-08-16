"""Typed error hierarchy for the LLM layer.

The rest of the application catches these error types - never provider- or
vendor-specific exceptions. Providers may raise the specific subclasses
directly; the base client also maps common builtin failures (timeout,
connection) onto them.
"""

from typing import Any


class LLMError(Exception):
    """Base class for all LLM layer errors."""


class LLMConfigurationError(LLMError):
    """Missing or invalid provider configuration.

    Raised when no LLM_PROVIDER is set, when a provider cannot be
    constructed (e.g. missing API key for a real provider), etc.
    """


class LLMProviderUnavailableError(LLMError):
    """The configured provider could not be reached or is not registered.

    Covers network failures, provider outages, auth failures, and requests
    for a provider name that has no registered adapter.
    """


class LLMTimeoutError(LLMError):
    """The provider call exceeded its timeout."""


class LLMInvalidResponseError(LLMError):
    """The provider returned output that could not be used.

    Covers malformed output (not parseable JSON) and structured responses
    that fail validation against the requested schema.

    When the output was parseable JSON, ``raw`` carries the parsed payload
    and ``raw_text`` the original provider text, so callers can salvage
    partially valid responses (e.g. per-item recovery) instead of losing
    everything. Both default to empty so existing raise sites are
    unaffected.
    """

    def __init__(
        self,
        message: str,
        raw: Any = None,
        raw_text: str = "",
    ) -> None:
        super().__init__(message)
        self.raw = raw
        self.raw_text = raw_text
