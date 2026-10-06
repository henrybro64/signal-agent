"""Bounded log tail/follow with secret redaction (#82)."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from app.core.config import load_config
from app.core.secrets import (
    load_env_settings,
    resolve_provider_credentials,
    secret_env_keys,
    secret_values,
)
from app.tools.redaction import redact_value

from . import paths as runtime_paths


def collect_secrets(
    root: str | Path | None = None, *, config_path: str | Path | None = None
) -> tuple[str, ...]:
    """Collect secret values from process env and the local ``.env`` file."""

    env = load_env_settings(runtime_paths.env_path(root))
    providers = None
    try:
        providers = load_config(
            config_path or runtime_paths.config_path(root)
        ).providers
        env = resolve_provider_credentials(env, providers)
    except (OSError, ValueError):
        pass
    values = list(secret_values(env))
    # Also read the dotenv file directly so redaction works even when
    # process env already has empty placeholders (load_dotenv won't override).
    dotenv = runtime_paths.env_path(root)
    if dotenv.is_file():
        for line in dotenv.read_text(encoding="utf-8").splitlines():
            if "=" not in line or line.strip().startswith("#"):
                continue
            key, value = line.split("=", 1)
            if key.strip() in secret_env_keys(providers):
                parsed = _parse_dotenv_value(value)
                if parsed:
                    values.append(parsed)
    return tuple(value for value in values if value)


def read_log_tail(
    *,
    root: str | Path | None = None,
    lines: int = 50,
    path: str | Path | None = None,
    config_path: str | Path | None = None,
) -> list[str]:
    log_file = Path(path) if path is not None else runtime_paths.log_path(root)
    if lines < 0:
        raise ValueError("lines must be non-negative")
    if lines == 0:
        return []
    if not log_file.is_file():
        return []
    content = log_file.read_text(encoding="utf-8", errors="replace").splitlines()
    secrets = collect_secrets(root, config_path=config_path)
    return [redact_value(line, secrets) for line in content[-lines:]]


def format_log_lines(lines: Iterable[str]) -> str:
    return "\n".join(lines)


def _parse_dotenv_value(raw: str) -> str:
    """Strip surrounding single/double quotes from a dotenv assignment value."""

    value = raw.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        return value[1:-1]
    return value
