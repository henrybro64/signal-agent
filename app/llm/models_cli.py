"""Model management helpers for CLI and config (#86)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from app.core.config import LlmConfig, load_config
from app.core.secrets import load_env_settings
from app.llm import LLMRequest, create_llm_provider, supported_llm_providers
from app.llm.exceptions import LLMError
from app.llm.router import ModelRef, RoutedLLMProvider, classify_provider_failure
from app.runtime import paths as runtime_paths


def format_models_status(
    *,
    root: str | Path | None = None,
    config_path: str | Path | None = None,
) -> str:
    config = load_config(config_path or runtime_paths.config_path(root))
    primary = f"{config.llm.provider}/{config.llm.model}"
    lines = [
        f"default: {primary}",
        f"timeout_seconds: {config.llm.timeout_seconds}",
        f"max_retries: {config.llm.max_retries}",
    ]
    if config.llm.fallbacks:
        lines.append("fallbacks:")
        for item in config.llm.fallbacks:
            lines.append(f"  - {item.provider}/{item.model}")
    else:
        lines.append("fallbacks: (none)")
    return "\n".join(lines)


def set_default_model(
    model_id: str,
    *,
    root: str | Path | None = None,
    config_path: str | Path | None = None,
) -> str:
    ref = ModelRef.parse(model_id)
    _validate_model_ref(ref)
    path = Path(config_path or runtime_paths.config_path(root))
    payload = _load_yaml(path)
    llm = payload.setdefault("llm", {})
    if not isinstance(llm, dict):
        raise ValueError("llm config must be a mapping")
    llm["provider"] = ref.provider
    llm["model"] = ref.model
    scoring = payload.setdefault("scoring", {})
    if isinstance(scoring, dict):
        scoring["llm_model"] = ref.model
    _write_yaml(path, payload)
    # Validate via loader.
    load_config(path)
    return str(ref)


def add_fallback_model(
    model_id: str,
    *,
    root: str | Path | None = None,
    config_path: str | Path | None = None,
) -> list[str]:
    ref = ModelRef.parse(model_id)
    _validate_model_ref(ref)
    path = Path(config_path or runtime_paths.config_path(root))
    payload = _load_yaml(path)
    llm = payload.setdefault("llm", {})
    if not isinstance(llm, dict):
        raise ValueError("llm config must be a mapping")
    fallbacks = llm.setdefault("fallbacks", [])
    if not isinstance(fallbacks, list):
        raise ValueError("llm.fallbacks must be a list")
    entry = {"provider": ref.provider, "model": ref.model}
    if entry not in fallbacks and f"{ref.provider}/{ref.model}" not in fallbacks:
        fallbacks.append(entry)
    _write_yaml(path, payload)
    config = load_config(path)
    return [f"{item.provider}/{item.model}" for item in config.llm.fallbacks]


def check_model_health(
    *,
    root: str | Path | None = None,
    config_path: str | Path | None = None,
    provider: Any | None = None,
) -> dict[str, Any]:
    """Run the smallest safe request against the configured/fake provider."""

    config = load_config(config_path or runtime_paths.config_path(root))
    env = load_env_settings(runtime_paths.env_path(root))
    router = provider or build_routed_provider(config.llm, api_key=env.openai_api_key)
    try:
        response = router.generate(
            LLMRequest(
                instructions="Reply with exactly: ok",
                input="ping",
                temperature=0,
            )
        )
        served = getattr(router, "last_served", None)
        return {
            "ok": True,
            "text_preview": (response.text or "")[:80],
            "served": (
                str(served) if served else f"{config.llm.provider}/{config.llm.model}"
            ),
            "failure_class": None,
        }
    except LLMError as exc:
        message = str(exc)[:200]
        for secret in (
            env.openai_api_key,
            env.telegram_bot_token,
            env.telegram_chat_id,
        ):
            if secret:
                message = message.replace(secret, "***")
        # Belt-and-suspenders for accidental key-shaped fragments in errors.
        if "sk-" in message:
            message = "authentication failed (details redacted)"
        failure_class = getattr(router, "last_failure_class", None) or (
            classify_provider_failure(exc)
        )
        return {
            "ok": False,
            "text_preview": "",
            "served": None,
            "failure_class": failure_class,
            "error_type": type(exc).__name__,
            "error": message,
        }


def build_routed_provider(
    llm: LlmConfig,
    *,
    api_key: str | None,
    provider_factory: Any = create_llm_provider,
) -> RoutedLLMProvider:
    return RoutedLLMProvider(
        primary=ModelRef(provider=llm.provider, model=llm.model),
        fallbacks=[
            ModelRef(provider=item.provider, model=item.model) for item in llm.fallbacks
        ],
        api_key=api_key,
        timeout_seconds=llm.timeout_seconds,
        temperature=llm.temperature,
        max_retries=llm.max_retries,
        provider_factory=provider_factory,
    )


def _validate_model_ref(ref: ModelRef) -> None:
    if ref.provider == "ollama":
        raise ValueError(
            "Ollama provider is reserved but not implemented yet; "
            "use provider 'openai' or 'fake'"
        )
    if ref.provider not in supported_llm_providers():
        supported = ", ".join(sorted(supported_llm_providers()))
        raise ValueError(
            f"Unknown LLM provider {ref.provider!r}; supported: {supported}"
        )


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise FileNotFoundError(f"Config file not found: {path}")
    payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(payload, dict):
        raise ValueError("Config root must be a mapping")
    return payload


def _write_yaml(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(
        yaml.safe_dump(payload, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
