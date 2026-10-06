"""Framework-neutral LLM provider gateway.

Dependency direction: scorers/tasks depend on these interfaces; provider
adapters may call HTTP/SDKs. Do not import LangChain here.
"""

from .anthropic_provider import AnthropicProvider
from .base import LLMProvider
from .capabilities import ProviderCapabilities
from .exceptions import (
    LLMAuthError,
    LLMError,
    LLMNetworkError,
    LLMProviderError,
    LLMRateLimitError,
    LLMResponseError,
    LLMTimeoutError,
    UnsupportedCapabilityError,
)
from .factory import (
    create_llm_provider,
    default_model_for_provider,
    supported_llm_providers,
)
from .fake_provider import FakeLLMProvider
from .models import (
    LLMMessage,
    LLMRequest,
    LLMResponse,
    LLMUsage,
    StructuredOutputSpec,
    ToolDefinition,
)
from .openai_provider import DEFAULT_OPENAI_RESPONSES_URL, OpenAIProvider
from .router import ModelRef, RoutedLLMProvider, classify_provider_failure

__all__ = [
    "AnthropicProvider",
    "ProviderCapabilities",
    "UnsupportedCapabilityError",
    "LLMUsage",
    "ToolDefinition",
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
    "default_model_for_provider",
    "supported_llm_providers",
]
