"""Gemini LLM provider adapter.

Implements the Google Gemini generateContent API behind the existing
LLMClient abstraction: subclasses implement ONLY ``_complete()``; JSON
parsing, markdown-fence tolerance, schema validation, and typed error
mapping all live in the base class (app/llm/base.py) - nothing is
duplicated here.

- Configuration comes exclusively from backend environment variables
  (LLM_PROVIDER=gemini, GEMINI_API_KEY, GEMINI_MODEL, GEMINI_BASE_URL,
  GEMINI_TIMEOUT_SECONDS) - the same Gemini key already used by the
  Gemini search-discovery provider.
- The API key never leaves the backend, is never logged, and never
  appears in error messages or reprs. The application starts without any
  of these set; clients are built lazily by get_client() and missing
  configuration raises a typed LLMConfigurationError at call time, never
  at startup.
- The adapter makes exactly one HTTP POST per call. It has no browsing,
  search, or tool access: it can only answer from the prompt it is given
  (which the evidence-based ExtractionService builds from supplied
  evidence).
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

DEFAULT_MODEL = "gemini-flash-latest"
DEFAULT_BASE_URL = "https://generativelanguage.googleapis.com"
# Gemini REST endpoint path prefix (model name + :generateContent appended).
API_VERSION = "/v1beta"


class GeminiClient(LLMClient):
    """Google Gemini generateContent adapter."""

    provider = "gemini"

    def __init__(
        self,
        *,
        api_key: str = "",
        api_keys: list[str] | None = None,
        model: str = DEFAULT_MODEL,
        base_url: str = DEFAULT_BASE_URL,
        timeout_seconds: float = 20.0,
        http_client: httpx.Client | None = None,
    ) -> None:
        keys = [k.strip() for k in (api_keys or []) if k and k.strip()]
        if api_key and not keys:
            keys = [api_key.strip()]
        keys = [k for k in keys if k]
        if not keys:
            raise LLMConfigurationError(
                "GEMINI_API_KEY is not set: the gemini provider requires it "
                "(backend environment only)."
            )
        self._api_keys = keys
        self._model = (model or "").strip() or DEFAULT_MODEL
        self._base_url = (base_url or "").rstrip("/") or DEFAULT_BASE_URL
        self._timeout_seconds = timeout_seconds
        self._client = http_client or httpx.Client(timeout=timeout_seconds)
        self._owns_client = http_client is None

    @classmethod
    def from_settings(cls, settings: object | None = None) -> "GeminiClient":
        """Build the client from backend environment settings."""
        if settings is None:
            from app.config import settings as app_settings

            settings = app_settings
        return cls(
            api_keys=getattr(settings, "gemini_api_keys", None),
            model=getattr(settings, "GEMINI_MODEL", ""),
            base_url=getattr(settings, "GEMINI_BASE_URL", ""),
            timeout_seconds=getattr(settings, "GEMINI_TIMEOUT_SECONDS", 20.0),
        )

    def _complete(
        self,
        prompt: str,
        *,
        system_prompt: str = "",
        temperature: float | None = None,
        timeout_seconds: float | None = None,
    ) -> str:
        """One generateContent call; returns candidates[0].content.parts[0].text."""
        contents: list[dict] = [
            {"role": "user", "parts": [{"text": prompt}]}
        ]
        payload: dict = {"contents": contents}
        if system_prompt:
            payload["systemInstruction"] = {
                "parts": [{"text": system_prompt}]
            }
        if temperature is not None:
            payload.setdefault("generationConfig", {})[
                "temperature"
            ] = temperature

        try:
            response = self._client.post(
                f"{self._base_url}{API_VERSION}"
                f"/models/{self._model}:generateContent",
                json=payload,
                headers={
                    "x-goog-api-key": self._api_keys[0],
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

        # Rotate around the configured API keys on rate limits: each key has
        # its own free-tier quota, so a 429 on one key is retried immediately
        # with the next key before the retry/backoff layer is even consulted.
        if response.status_code == 429 and len(self._api_keys) > 1:
            for alt_key in self._api_keys[1:]:
                try:
                    alt = self._client.post(
                        f"{self._base_url}{API_VERSION}"
                        f"/models/{self._model}:generateContent",
                        json=payload,
                        headers={
                            "x-goog-api-key": alt_key,
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
                if alt.status_code == 429:
                    continue
                if alt.status_code == 401 or alt.status_code == 403:
                    continue
                if alt.status_code != 200:
                    continue
                response = alt
                break
            else:
                raise LLMProviderUnavailableError(
                    f"{self.provider}: rate limit hit on all configured API keys "
                    "(HTTP 429). Retry later or lower the request rate."
                )

        if response.status_code in (400, 401, 403):
            raise LLMProviderUnavailableError(
                f"{self.provider}: authentication failed (HTTP "
                f"{response.status_code}). Check GEMINI_API_KEY."
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
            text = body["candidates"][0]["content"]["parts"][0]["text"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMInvalidResponseError(
                f"{self.provider}: response body is missing "
                "'candidates[0].content.parts[0].text'"
            ) from exc
        if not isinstance(text, str) or not text.strip():
            raise LLMInvalidResponseError(
                f"{self.provider}: response contained an empty message content"
            )
        return text

    def __repr__(self) -> str:
        # Never leak the API keys in reprs/logging.
        return (
            f"GeminiClient(provider={self.provider!r}, model={self._model!r}, "
            f"base_url={self._base_url!r}, api_keys={len(self._api_keys)} (***))"
        )

    def close(self) -> None:
        if self._owns_client:
            self._client.close()
