"""LLM provider adapters.

Each provider is isolated in its own module and must only implement
LLMClient._complete() (see app/llm/base.py). Adapters self-register here.

The offline "fake" provider is always available (no API key needed) for
tests and local demos. The real "deepseek" and "openrouter" providers
(official chat/completions APIs) are constructed lazily by get_client() from
backend environment variables only - the application never requires an API
key to start, and a missing key surfaces as a typed LLMConfigurationError
when a client is actually requested.
"""

from app.llm.base import register_provider
from app.llm.providers.deepseek import DeepSeekClient
from app.llm.providers.fake import FakeLLMClient
from app.llm.providers.gemini import GeminiClient
from app.llm.providers.nvidia import NvidiaClient
from app.llm.providers.openrouter import OpenRouterClient

register_provider("fake", FakeLLMClient)
register_provider("deepseek", DeepSeekClient.from_settings)
register_provider("gemini", GeminiClient.from_settings)
register_provider("nvidia", NvidiaClient.from_settings)
register_provider("openrouter", OpenRouterClient.from_settings)
