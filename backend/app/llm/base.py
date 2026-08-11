"""Provider-agnostic LLM client abstraction.

The rest of the application talks ONLY to LLMClient and its typed request
models. Provider-specific code (API endpoints, auth, model names) is confined
to the abstract `_complete()` implementation inside app/llm/providers/ - no
DeepSeek/OpenAI/Gemini details leak into the pipeline or API layer.

Configuration comes from environment variables (see app/config.py):
  LLM_PROVIDER, LLM_API_KEY, LLM_MODEL, LLM_TIMEOUT_SECONDS.
The application starts fine with no key configured; clients are created
lazily via get_client().
"""

from __future__ import annotations

import json
import re
from abc import ABC, abstractmethod
from typing import Any, Callable, Type

from pydantic import BaseModel, ValidationError

from app.config import settings
from app.core.domain import Classification
from app.llm.errors import (
    LLMConfigurationError,
    LLMError,
    LLMInvalidResponseError,
    LLMProviderUnavailableError,
    LLMTimeoutError,
)
from app.llm.types import (
    ClassificationRequest,
    CompletionRequest,
    DescriptionRequest,
    ExtractionRequest,
    ExtractedAttributes,
    GeneratedDescription,
    LLMRequest,
    StructuredCompletionRequest,
    StructuredRequest,
)


class LLMClient(ABC):
    """Interface every provider adapter implements.

    Subclasses implement ONLY `_complete()`. All structured operations
    (extraction, classification, description generation, generic structured
    completion) are implemented here on top of it, including JSON parsing,
    schema validation, and error mapping - so every provider behaves the
    same way towards the application.
    """

    provider: str

    @abstractmethod
    def _complete(
        self,
        prompt: str,
        *,
        system_prompt: str = "",
        temperature: float | None = None,
        timeout_seconds: float | None = None,
    ) -> str:
        """Raw completion - the single provider-specific hook.

        Implementations own the vendor call: endpoint, auth, model selection.
        They may raise the LLM layer's typed errors (see app.llm.errors).
        """

    # --- free-form ---------------------------------------------------------

    def complete(self, request: CompletionRequest) -> str:
        """Free-form completion; returns the raw provider text."""
        return self._call(request, request.user_prompt)

    # --- structured operations ---------------------------------------------

    def extract(self, request: ExtractionRequest) -> ExtractedAttributes:
        """Structured attribute extraction from a text document."""
        prompt = request.user_prompt or (
            "Extract all product attributes from the following text. Return "
            "JSON matching the requested schema (fields: name, raw_value, "
            "unit, confidence). Do not invent facts:\n" + request.text
        )
        return self._structured(request, request.output_schema, prompt)

    def classify(self, request: ClassificationRequest) -> Classification:
        """Classify product information into the internal Classification model."""
        prompt = request.user_prompt or (
            "Classify the following product information into the fields "
            "department, class, fine, classpath and product_type. Return JSON "
            "matching the requested schema. Do not invent facts:\n"
            + request.text
        )
        return self._structured(request, request.output_schema, prompt)

    def generate_description(self, request: DescriptionRequest) -> str:
        """Generate one commerce-ready description variant; returns its text."""
        attributes = "\n".join(
            f"- {key}: {value}" for key, value in request.attributes.items()
        )
        prompt = request.user_prompt or (
            f"Write a commerce-ready {request.target} description for this "
            f"product. Use ONLY the following known attributes and do not "
            f"invent facts:\n{attributes}\n"
            "Return JSON matching the requested schema with a single field "
            "text."
        )
        result = self._structured(request, request.output_schema, prompt)
        return result.text

    def structured_completion(self, request: StructuredCompletionRequest) -> BaseModel:
        """Generic structured completion validated against an arbitrary schema."""
        return self._structured(request, request.output_schema, request.user_prompt)

    # --- shared plumbing ----------------------------------------------------

    def _structured(
        self,
        request: StructuredRequest,
        schema: Type[BaseModel],
        prompt: str,
    ) -> BaseModel:
        """Run a completion and validate the parsed output against `schema`."""
        raw = self._call(request, prompt)
        data = self._parse_json(raw)
        return self._validate(data, schema)

    def _call(self, request: LLMRequest, prompt: str) -> str:
        """Invoke the provider hook and map failures onto the typed errors."""
        try:
            return self._complete(
                prompt,
                system_prompt=request.system_prompt,
                temperature=request.temperature,
                timeout_seconds=request.timeout_seconds,
            )
        except LLMError:
            raise
        except TimeoutError as exc:
            raise LLMTimeoutError(
                f"{self.provider}: provider call timed out"
            ) from exc
        except ConnectionError as exc:
            raise LLMProviderUnavailableError(
                f"{self.provider}: provider unreachable: {exc}"
            ) from exc
        except Exception as exc:
            raise LLMProviderUnavailableError(
                f"{self.provider}: provider call failed: {exc}"
            ) from exc

    def _parse_json(self, raw: str) -> Any:
        """Parse model output, tolerating markdown code fences."""
        text = raw.strip()
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
            text = re.sub(r"\s*```$", "", text)
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise LLMInvalidResponseError(
                f"{self.provider}: malformed model output - not valid JSON"
            ) from exc

    @staticmethod
    def _validate(data: Any, schema: Type[BaseModel]) -> BaseModel:
        try:
            return schema.model_validate(data)
        except ValidationError as exc:
            raise LLMInvalidResponseError(
                f"structured response failed validation against "
                f"{schema.__name__}: {exc}"
            ) from exc


# --- provider registry -------------------------------------------------------

PROVIDER_FACTORIES: dict[str, Callable[[], LLMClient]] = {}
_clients: dict[str, LLMClient] = {}


def register_provider(name: str, factory: Callable[[], LLMClient]) -> None:
    """Register a provider factory under a name (see LLM_PROVIDER)."""
    PROVIDER_FACTORIES[name] = factory


def get_client(provider: str | None = None) -> LLMClient:
    """Create (or return the cached) client for a configured provider.

    Uses LLM_PROVIDER from the environment unless `provider` is given.
    Raises LLMConfigurationError when nothing is configured, and
    LLMProviderUnavailableError when the name has no registered adapter.
    No API call happens here.
    """
    name = provider or settings.llm_provider
    if not name:
        raise LLMConfigurationError(
            "No LLM provider configured. Set LLM_PROVIDER in .env "
            "(e.g. LLM_PROVIDER=fake for offline use). Real providers only "
            "require an API key once they are registered."
        )
    if name in _clients:
        return _clients[name]
    factory = PROVIDER_FACTORIES.get(name)
    if factory is None:
        raise LLMProviderUnavailableError(
            f"LLM provider {name!r} is not registered. "
            f"Registered: {sorted(PROVIDER_FACTORIES)}"
        )
    try:
        client = factory()
    except Exception as exc:
        raise LLMConfigurationError(
            f"Failed to create LLM provider {name!r}: {exc}"
        ) from exc
    _clients[name] = client
    return client
