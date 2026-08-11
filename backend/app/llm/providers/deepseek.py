"""DeepSeek LLM provider adapter (Step 6C).

Implements the official DeepSeek chat/completions API (OpenAI-compatible)
behind the existing LLMClient abstraction: subclasses implement ONLY
``_complete()``; JSON parsing, markdown-fence tolerance, schema validation,
and typed error mapping all live in the base class.

- Configuration comes exclusively from backend environment variables
  (LLM_PROVIDER, LLM_API_KEY, LLM_MODEL, LLM_BASE_URL, LLM_TIMEOUT_SECONDS).
- The API key never leaves the backend, is never logged, and never appears in
  error messages or reprs. The application starts without any of these set;
  clients are built lazily by get_client() and missing configuration raises a
  typed LLMConfigurationError at call time, never at startup.
- The adapter makes exactly one HTTP POST per call. It has no browsing,
  search, or tool access: it can only answer from the prompt it is given
  (which the evidence-based ExtractionService builds from supplied evidence).
"""

from __future__ import annotations

import httpx

from app.config import settings
from app.llm.base import LLMClient
from app.llm.errors import (
    LLMConfigurationError,
    LLMInvalidResponseError,
    LLMProviderUnavailableError,
    LLMTimeoutError,
)

DEFAULT_MODEL = "deepseek-chat"
DEFAULT_BASE_URL = "https://api.deepseek.com"
CHAT_COMPLETIONS_PATH = "/chat/completions"


class DeepSeekClient(LLMClient):
    """Official DeepSeek chat/completions adapter."""

    provider = "deepseek"

    def __init__(
        self,
        *,
        api_key: str,
        model: str = DEFAULT_MODEL,
        base_url: str = DEFAULT_BASE_URL,
        timeout_seconds: float = 30.0,
        http_client: httpx.Client | None = None,
    ) -> None:
        key = (api_key or "").strip()
        if not key:
            raise LLMConfigurationError(
                "LLM_API_KEY is not set: the deepseek provider requires it "
                "(backend environment only)."
            )
        self._api_key = key
        self._model = (model or "").strip() or DEFAULT_MODEL
        self._base_url = (base_url or "").rstrip("/") or DEFAULT_BASE_URL
        self._timeout_seconds = timeout_seconds
        self._client = http_client or httpx.Client(timeout=timeout_seconds)
        self._owns_client = http_client is None

    @classmethod
    def from_settings(cls, settings: object | None = None) -> "DeepSeekClient":
        """Build the client from backend environment settings."""
        if settings is None:
            from app.config import settings as app_settings

            settings = app_settings
        return cls(
            api_key=getattr(settings, "llm_api_key", ""),
            model=getattr(settings, "llm_model", ""),
            base_url=getattr(settings, "llm_base_url", ""),
            timeout_seconds=getattr(settings, "llm_timeout_seconds", 30.0),
        )

    def _complete(
        self,
        prompt: str,
        *,
        system_prompt: str = "",
        temperature: float | None = None,
        timeout_seconds: float | None = None,
    ) -> str:
        """One official chat/completions call; returns choices[0].message.content."""
        messages: list[dict] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        payload: dict = {"model": self._model, "messages": messages}
        if temperature is not None:
            payload["temperature"] = temperature

        try:
            response = self._client.post(
                f"{self._base_url}{CHAT_COMPLETIONS_PATH}",
                json=payload,
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                },
                timeout=timeout_seconds or self._timeout_seconds,
            )
        except httpx.TimeoutException as exc:
            raise LLMTimeoutError(
                f"{self.provider}: provider call timed out"
            ) from exc
        except httpx.TransportError as exc:
            raise LLMProviderUnavailableError(
                f"{self.provider}: provider unreachable: {exc}"
            ) from exc

        if response.status_code == 401 or response.status_code == 403:
            raise LLMProviderUnavailableError(
                f"{self.provider}: authentication failed (HTTP "
                f"{response.status_code}). Check LLM_API_KEY."
            )
        if response.status_code == 429:
            raise LLMProviderUnavailableError(
                f"{self.provider}: rate limit hit (HTTP 429). "
                "Retry later or lower the request rate."
            )
        if response.status_code != 200:
            raise LLMProviderUnavailableError(
                f"{self.provider}: provider returned HTTP "
                f"{response.status_code}"
            )

        try:
            body = response.json()
        except ValueError as exc:
            raise LLMInvalidResponseError(
                f"{self.provider}: provider returned a non-JSON response body"
            ) from exc

        try:
            content = body["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMInvalidResponseError(
                f"{self.provider}: response body is missing "
                "'choices[0].message.content'"
            ) from exc
        if not isinstance(content, str) or not content.strip():
            raise LLMInvalidResponseError(
                f"{self.provider}: response contained an empty message content"
            )
        return content

    def __repr__(self) -> str:
        # Never leak the API key in reprs/logging.
        return (
            f"DeepSeekClient(provider={self.provider!r}, model={self._model!r}, "
            f"base_url={self._base_url!r}, api_key=***)"
        )

    def close(self) -> None:
        if self._owns_client:
            self._client.close()
