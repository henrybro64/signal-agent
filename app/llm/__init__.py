"""Framework-neutral LLM provider gateway.

Dependency direction: scorers/tasks depend on these interfaces; provider
adapters may call HTTP/SDKs. Do not import LangChain here.
"""

from .base import LLMProvider
from .exceptions import (
    LLMAuthError,
    LLMError,
    LLMNetworkError,
    LLMProviderError,
    LLMRateLimitError,
    LLMResponseError,
    LLMTimeoutError,
)
from .factory import create_llm_provider, supported_llm_providers
from .fake_provider import FakeLLMProvider
from .models import LLMMessage, LLMRequest, LLMResponse, StructuredOutputSpec
from .openai_provider import DEFAULT_OPENAI_RESPONSES_URL, OpenAIProvider
from .router import ModelRef, RoutedLLMProvider, classify_provider_failure

__all__ = [
    "DEFAULT_OPENAI_RESPONSES_URL",
    "FakeLLMProvider",
    "LLMAuthError",
    "LLMError",
    "LLMMessage",
    "LLMNetworkError",
    "LLMProvider",
    "LLMProviderError",
    "LLMRateLimitError",
    "LLMRequest",
    "LLMResponse",
    "LLMResponseError",
    "LLMTimeoutError",
    "ModelRef",
    "OpenAIProvider",
    "RoutedLLMProvider",
    "StructuredOutputSpec",
    "classify_provider_failure",
    "create_llm_provider",
    "supported_llm_providers",
]
