"""LLM abstraction package.

Public surface: get_client() for the application, LLMClient for provider
implementations, typed request/response models, and the typed error
hierarchy. Importing this package registers the available providers.
"""

from app.llm import providers  # noqa: F401 - side effect: provider registration
from app.llm.base import LLMClient, get_client, register_provider
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
    ExtractedAttribute,
    ExtractedAttributes,
    ExtractionRequest,
    GeneratedDescription,
    LLMRequest,
    StructuredCompletionRequest,
    StructuredRequest,
)
from app.llm.providers.fake import FakeLLMClient
from app.llm.providers.deepseek import DeepSeekClient
from app.llm.providers.gemini import GeminiClient
from app.llm.providers.nvidia import NvidiaClient
from app.llm.providers.openrouter import OpenRouterClient

__all__ = [
    "ClassificationRequest",
    "CompletionRequest",
    "DeepSeekClient",
    "DescriptionRequest",
    "ExtractedAttribute",
    "ExtractedAttributes",
    "ExtractionRequest",
    "FakeLLMClient",
    "GeminiClient",
    "GeneratedDescription",
    "LLMClient",
    "LLMConfigurationError",
    "LLMError",
    "LLMInvalidResponseError",
    "LLMProviderUnavailableError",
    "LLMRequest",
    "LLMTimeoutError",
    "NvidiaClient",
    "OpenRouterClient",
    "StructuredCompletionRequest",
    "StructuredRequest",
    "get_client",
    "register_provider",
]
