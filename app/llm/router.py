"""LLM provider routing with retries and explicit fallbacks (#86)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.core.logging import get_logger, log_event

from .exceptions import (
    LLMAuthError,
    LLMError,
    LLMNetworkError,
    LLMProviderError,
    LLMRateLimitError,
    LLMTimeoutError,
)
from .factory import create_llm_provider
from .models import LLMRequest, LLMResponse

_LOGGER = get_logger("llm.router")


@dataclass(slots=True)
class ModelRef:
    provider: str
    model: str

    @classmethod
    def parse(cls, value: str) -> ModelRef:
        provider, sep, model = value.partition("/")
        if not sep or not provider.strip() or not model.strip():
            raise ValueError(f"Model id must look like provider/model, got {value!r}")
        return cls(provider=provider.strip().lower(), model=model.strip())

    def __str__(self) -> str:
        return f"{self.provider}/{self.model}"


@dataclass(slots=True)
class RoutedLLMProvider:
    """Try primary then configured fallbacks; never silent about switches."""

    primary: ModelRef
    fallbacks: list[ModelRef] = field(default_factory=list)
    api_key: str | None = None
    timeout_seconds: float = 20.0
    temperature: float = 0.2
    max_retries: int = 1
    provider_factory: Any = create_llm_provider
    last_served: ModelRef | None = None
    last_failure_class: str | None = None
    _providers: dict[str, Any] = field(default_factory=dict, repr=False, compare=False)

    @property
    def name(self) -> str:
        # Surface the configured primary provider so existing call sites that
        # assert on provider name keep working when no fallback is used.
        return self.primary.provider

    def _provider_for(self, ref: ModelRef) -> Any:
        key = str(ref)
        cached = self._providers.get(key)
        if cached is not None:
            return cached
        provider = self.provider_factory(
            provider=ref.provider,
            api_key=self.api_key,
            model=ref.model,
            timeout_seconds=self.timeout_seconds,
            temperature=self.temperature,
        )
        self._providers[key] = provider
        return provider

    def generate(self, request: LLMRequest) -> LLMResponse:
        chain = [self.primary, *self.fallbacks]
        errors: list[str] = []
        last_exc: LLMError | None = None
        for index, ref in enumerate(chain):
            attempts = max(1, self.max_retries + 1)
            for attempt in range(1, attempts + 1):
                try:
                    provider = self._provider_for(ref)
                    routed = LLMRequest(
                        instructions=request.instructions,
                        input=request.input,
                        messages=request.messages,
                        model=ref.model,
                        temperature=(
                            self.temperature
                            if request.temperature is None
                            else request.temperature
                        ),
                        timeout_seconds=(
                            self.timeout_seconds
                            if request.timeout_seconds is None
                            else request.timeout_seconds
                        ),
                        store=request.store,
                        structured_output=request.structured_output,
                    )
                    response = provider.generate(routed)
                    self.last_served = ref
                    self.last_failure_class = None
                    if index > 0:
                        log_event(
                            _LOGGER,
                            event="llm_fallback_used",
                            message="served by configured fallback",
                            provider=ref.provider,
                            model=ref.model,
                            primary=str(self.primary),
                        )
                    else:
                        log_event(
                            _LOGGER,
                            event="llm_primary_used",
                            message="served by primary model",
                            provider=ref.provider,
                            model=ref.model,
                        )
                    return response
                except LLMError as exc:
                    last_exc = exc
                    errors.append(f"{ref}#{attempt}:{type(exc).__name__}:{exc}")
                    if not _retryable(exc) or attempt >= attempts:
                        break
        failure_class = (
            classify_provider_failure(last_exc) if last_exc is not None else "unknown"
        )
        self.last_failure_class = failure_class
        # Single-provider chains re-raise the typed error so health/CLI keep
        # precise failure classes. Multi-provider chains wrap but preserve cause.
        if last_exc is not None and len(chain) == 1:
            raise last_exc
        raise LLMProviderError(
            "All providers/fallbacks failed: " + "; ".join(errors[-5:])
        ) from last_exc


def classify_provider_failure(exc: BaseException | None) -> str:
    """Return a stable failure class for CLI health output.

    Walks ``__cause__`` so router-wrapped terminal errors still surface
    auth/timeout/rate_limit/network instead of collapsing to ``provider``.
    """

    current: BaseException | None = exc
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        labeled = _failure_class_for_exception(current)
        if labeled is not None:
            return labeled
        current = current.__cause__ or current.__context__

    if isinstance(exc, LLMProviderError):
        return "provider"
    return "unknown"


def _failure_class_for_exception(exc: BaseException) -> str | None:
    typed = (
        (LLMAuthError, "auth"),
        (LLMRateLimitError, "rate_limit"),
        (LLMTimeoutError, "timeout"),
        (LLMNetworkError, "network"),
    )
    for exc_type, label in typed:
        if isinstance(exc, exc_type):
            return label
    return _failure_class_from_message(str(exc).lower())


def _failure_class_from_message(text: str) -> str | None:
    if (
        "unsupported" in text
        or "invalid model" in text
        or ("model" in text and "not" in text and "found" in text)
    ):
        return "unsupported_model"
    if "network" in text or "connection" in text:
        return "network"
    return None


def _retryable(exc: LLMError) -> bool:
    if isinstance(exc, LLMAuthError):
        return False
    return isinstance(
        exc,
        (LLMTimeoutError, LLMRateLimitError, LLMNetworkError, LLMProviderError),
    )
