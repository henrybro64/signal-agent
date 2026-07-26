"""Framework-neutral LLM provider gateway.

Dependency direction: scorers/tasks depend on these interfaces; provider
adapters may call HTTP/SDKs. Do not import LangChain here.
"""

from .base import LLMProvider
from .exceptions import (
    LLMError,
    LLMProviderError,
    LLMResponseError,
    LLMTimeoutError,
)
from .factory import create_llm_provider, supported_llm_providers
from .fake_provider import FakeLLMProvider
from .models import LLMMessage, LLMRequest, LLMResponse, StructuredOutputSpec
from .openai_provider import DEFAULT_OPENAI_RESPONSES_URL, OpenAIProvider

__all__ = [
    "DEFAULT_OPENAI_RESPONSES_URL",
    "FakeLLMProvider",
    "LLMError",
    "LLMMessage",
    "LLMProvider",
    "LLMProviderError",
    "LLMRequest",
    "LLMResponse",
    "LLMResponseError",
    "LLMTimeoutError",
    "OpenAIProvider",
    "StructuredOutputSpec",
    "create_llm_provider",
    "supported_llm_providers",
]
