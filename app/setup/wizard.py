"""Interactive / non-interactive first-run setup wizard (#81)."""

from __future__ import annotations

import getpass
import re
import shutil
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from app.core.db import init_db
from app.llm import supported_llm_providers
from app.runtime import paths as runtime_paths
from app.setup.browser import detect_browser
from app.setup.templates import DEFAULT_CONFIG_TEMPLATE, DEFAULT_ENV_TEMPLATE
from app.setup.validate import ValidationReport, validate_local_setup

PromptFn = Callable[[str, str], str]
ConfirmFn = Callable[[str, bool], bool]


@dataclass(slots=True)
class SetupOptions:
    provider: str = "fake"
    model: str = "gpt-4o-mini"
    llm_enabled: bool = False
    openai_api_key: str = ""
    telegram_enabled: bool = False
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    non_interactive: bool = False
    force: bool = False


@dataclass(slots=True)
class SetupResult:
    config_path: Path
    env_path: Path
    db_path: Path
    created_config: bool
    created_env: bool
    validation: ValidationReport
    browser_detail: str


def run_setup(
    *,
    root: str | Path | None = None,
    options: SetupOptions | None = None,
    prompt: PromptFn | None = None,
    confirm: ConfirmFn | None = None,
) -> SetupResult:
    """Create or update local config/env/db for first-run use."""

    opts = options or SetupOptions()
    base = runtime_paths.project_root(root)
    runtime_paths.ensure_local_dirs(base)
    cfg_path = runtime_paths.config_path(base)
    dotenv_path = runtime_paths.env_path(base)
    db = runtime_paths.db_path(base)

    merged = _collect_setup_options(
        opts,
        cfg_path=cfg_path,
        dotenv_path=dotenv_path,
        prompt=prompt,
        confirm=confirm,
    )
    _validate_provider_model(merged.provider, merged.model)
    created_config = _apply_config_file(
        cfg_path,
        merged,
        root=base,
        interactive=not opts.non_interactive,
        force=opts.force,
        confirm=confirm or _default_confirm,
    )
    created_env = _apply_env_file(
        dotenv_path,
        merged,
        root=base,
        force=opts.force,
    )

    init_db(db)
    browser = detect_browser()
    validation = validate_local_setup(
        root=base,
        config_path=cfg_path,
        env_path=dotenv_path,
    )
    return SetupResult(
        config_path=cfg_path,
        env_path=dotenv_path,
        db_path=db,
        created_config=created_config,
        created_env=created_env,
        validation=validation,
        browser_detail=browser.detail,
    )


def _collect_setup_options(
    opts: SetupOptions,
    *,
    cfg_path: Path,
    dotenv_path: Path,
    prompt: PromptFn | None,
    confirm: ConfirmFn | None,
) -> SetupOptions:
    # Under --force, skip parsing a possibly-malformed config so recovery works.
    existing_cfg: dict[str, Any] = {} if opts.force else _load_yaml(cfg_path)
    existing_env = _load_env_file(dotenv_path)
    merged = _merge_options(opts, existing_cfg, existing_env)
    if opts.non_interactive:
        return merged
    ask = prompt or _default_prompt
    # Custom prompt callables (tests) also receive secret prompts; the default
    # interactive path uses getpass so secrets are never echoed.
    ask_secret = prompt or _default_secret_prompt
    yes = confirm or _default_confirm
    return _interactive_collect(merged, existing_env, ask, ask_secret, yes)


def _apply_config_file(
    cfg_path: Path,
    merged: SetupOptions,
    *,
    root: Path,
    interactive: bool,
    force: bool,
    confirm: ConfirmFn,
) -> bool:
    if not cfg_path.exists():
        _write_config(cfg_path, merged)
        return True
    if force:
        _backup_file(cfg_path, root=root)
        _write_config(cfg_path, merged)
        return True
    if interactive and confirm(
        f"Replace {cfg_path.name} with generated defaults "
        f"(a backup will be kept under data/runtime/backups/)?",
        False,
    ):
        _backup_file(cfg_path, root=root)
        _write_config(cfg_path, merged)
        return True
    # Default / non-interactive: merge LLM/telegram keys only.
    _update_existing_config(cfg_path, merged)
    return False


def _apply_env_file(
    dotenv_path: Path,
    merged: SetupOptions,
    *,
    root: Path,
    force: bool,
) -> bool:
    env_existed = dotenv_path.exists()
    if env_existed and not force:
        _update_existing_env(dotenv_path, merged, preserve_missing=True)
        return False
    if env_existed and force:
        _backup_file(dotenv_path, root=root)
    _write_env(dotenv_path, merged)
    return not env_existed or force


def show_config_redacted(
    *,
    root: str | Path | None = None,
    config_path: str | Path | None = None,
    env_path: str | Path | None = None,
) -> str:
    """Return human-readable config + env presence with secrets redacted."""

    cfg_path = Path(config_path or runtime_paths.config_path(root))
    dotenv_path = Path(env_path or runtime_paths.env_path(root))
    lines = [f"config: {cfg_path}"]
    if cfg_path.is_file():
        text = cfg_path.read_text(encoding="utf-8")
        lines.append(text.rstrip())
    else:
        lines.append("(missing)")

    lines.append("")
    lines.append(f"env: {dotenv_path}")
    env_map = _load_env_file(dotenv_path)
    if not env_map:
        lines.append("(missing)")
    else:
        secret_keys = {
            "OPENAI_API_KEY",
            "TELEGRAM_BOT_TOKEN",
            "TELEGRAM_CHAT_ID",
            "LINKEDIN_EMAIL_IMAP_PASSWORD",
        }
        for key in sorted(env_map):
            value = env_map[key]
            if key in secret_keys:
                shown = "***" if value else "(empty)"
            else:
                shown = value or "(empty)"
            lines.append(f"{key}={shown}")
    return "\n".join(lines)


def _interactive_collect(
    opts: SetupOptions,
    existing_env: dict[str, str],
    prompt: PromptFn,
    secret_prompt: PromptFn,
    confirm: ConfirmFn,
) -> SetupOptions:
    providers = ", ".join(sorted(supported_llm_providers()))
    provider = prompt(
        f"LLM provider [{providers}]",
        opts.provider,
    ).strip().lower() or opts.provider
    model = prompt("LLM model", opts.model).strip() or opts.model
    llm_enabled = confirm(
        "Enable LLM scoring by default?",
        opts.llm_enabled,
    )
    openai_key = opts.openai_api_key
    if provider == "openai":
        current = "(set)" if existing_env.get("OPENAI_API_KEY") else "(empty)"
        entered = secret_prompt(
            f"OPENAI_API_KEY {current} — enter to keep, or paste new value",
            "",
        )
        if entered.strip():
            openai_key = entered.strip()
        else:
            openai_key = existing_env.get("OPENAI_API_KEY", "")

    telegram_enabled = confirm(
        "Enable Telegram notifications?",
        opts.telegram_enabled,
    )
    bot = opts.telegram_bot_token
    chat = opts.telegram_chat_id
    if telegram_enabled:
        entered_bot = secret_prompt(
            "TELEGRAM_BOT_TOKEN (enter keeps existing)",
            "",
        )
        if entered_bot.strip():
            bot = entered_bot.strip()
        else:
            bot = existing_env.get("TELEGRAM_BOT_TOKEN", "")
        entered_chat = secret_prompt(
            "TELEGRAM_CHAT_ID (enter keeps existing)",
            "",
        )
        if entered_chat.strip():
            chat = entered_chat.strip()
        else:
            chat = existing_env.get("TELEGRAM_CHAT_ID", "")

    return SetupOptions(
        provider=provider,
        model=model,
        llm_enabled=llm_enabled,
        openai_api_key=openai_key,
        telegram_enabled=telegram_enabled,
        telegram_bot_token=bot,
        telegram_chat_id=chat,
        non_interactive=False,
        force=opts.force,
    )


def _merge_options(
    opts: SetupOptions,
    existing_cfg: dict[str, Any],
    existing_env: dict[str, str],
) -> SetupOptions:
    llm = _as_dict(existing_cfg.get("llm"))
    scoring = _as_dict(existing_cfg.get("scoring"))
    telegram = _as_dict(_as_dict(existing_cfg.get("notifiers")).get("telegram"))
    provider, model, llm_enabled, telegram_enabled = _resolve_merge_flags(
        opts, llm, scoring, telegram
    )
    return SetupOptions(
        provider=provider,
        model=model,
        llm_enabled=llm_enabled,
        openai_api_key=opts.openai_api_key or existing_env.get("OPENAI_API_KEY", ""),
        telegram_enabled=telegram_enabled,
        telegram_bot_token=(
            opts.telegram_bot_token or existing_env.get("TELEGRAM_BOT_TOKEN", "")
        ),
        telegram_chat_id=(
            opts.telegram_chat_id or existing_env.get("TELEGRAM_CHAT_ID", "")
        ),
        non_interactive=opts.non_interactive,
        force=opts.force,
    )


def _resolve_merge_flags(
    opts: SetupOptions,
    llm: dict[str, Any],
    scoring: dict[str, Any],
    telegram: dict[str, Any],
) -> tuple[str, str, bool, bool]:
    if opts.non_interactive:
        provider = opts.provider or str(llm.get("provider") or "fake")
        model = opts.model or str(llm.get("model") or "gpt-4o-mini")
        return provider, model, opts.llm_enabled, opts.telegram_enabled
    provider = str(llm.get("provider") or opts.provider or "fake")
    model = str(llm.get("model") or opts.model or "gpt-4o-mini")
    llm_enabled = bool(scoring.get("llm_enabled", opts.llm_enabled))
    telegram_enabled = bool(telegram.get("enabled", opts.telegram_enabled))
    return provider, model, llm_enabled, telegram_enabled


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _validate_provider_model(provider: str, model: str) -> None:
    providers = supported_llm_providers()
    if provider not in providers:
        raise ValueError(
            f"Unsupported provider {provider!r}; "
            f"supported: {', '.join(sorted(providers))}"
        )
    if not model.strip():
        raise ValueError("model must be a non-empty string")


def _write_config(path: Path, opts: SetupOptions) -> None:
    text = DEFAULT_CONFIG_TEMPLATE.format(
        provider=opts.provider,
        model=opts.model,
        llm_enabled="true" if opts.llm_enabled else "false",
        telegram_enabled="true" if opts.telegram_enabled else "false",
    )
    path.write_text(text, encoding="utf-8")


def _update_existing_config(path: Path, opts: SetupOptions) -> None:
    if not path.is_file():
        _write_config(path, opts)
        return
    payload = _load_yaml(path)
    llm = payload.setdefault("llm", {})
    if not isinstance(llm, dict):
        llm = {}
        payload["llm"] = llm
    llm["provider"] = opts.provider
    llm["model"] = opts.model
    scoring = payload.setdefault("scoring", {})
    if isinstance(scoring, dict):
        scoring["llm_enabled"] = opts.llm_enabled
        scoring["llm_model"] = opts.model
    notifiers = payload.setdefault("notifiers", {})
    if isinstance(notifiers, dict):
        telegram = notifiers.setdefault("telegram", {})
        if isinstance(telegram, dict):
            telegram["enabled"] = opts.telegram_enabled
            telegram["failure_alerts_enabled"] = opts.telegram_enabled
    path.write_text(
        yaml.safe_dump(payload, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )


def _write_env(path: Path, opts: SetupOptions) -> None:
    text = DEFAULT_ENV_TEMPLATE.format(
        openai_api_key=opts.openai_api_key,
        telegram_bot_token=opts.telegram_bot_token,
        telegram_chat_id=opts.telegram_chat_id,
    )
    path.write_text(text, encoding="utf-8")


def _update_existing_env(
    path: Path,
    opts: SetupOptions,
    *,
    preserve_missing: bool,
) -> None:
    """Update known keys in-place while preserving comments and blank lines."""

    if not path.is_file():
        _write_env(path, opts)
        return

    current = _load_env_file(path)
    updates = {
        "OPENAI_API_KEY": opts.openai_api_key,
        "TELEGRAM_BOT_TOKEN": opts.telegram_bot_token,
        "TELEGRAM_CHAT_ID": opts.telegram_chat_id,
        "SIGNAL_AGENT_ENV": current.get("SIGNAL_AGENT_ENV") or "development",
    }
    for key, value in list(updates.items()):
        if preserve_missing and not value and current.get(key):
            updates[key] = current[key]

    original_lines = path.read_text(encoding="utf-8").splitlines()
    seen: set[str] = set()
    output: list[str] = []
    for line in original_lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            output.append(line)
            continue
        key, _existing = stripped.split("=", 1)
        key = key.strip()
        if key in updates:
            output.append(f"{key}={updates[key]}")
            seen.add(key)
        else:
            output.append(line)
    for key, value in updates.items():
        if key not in seen:
            output.append(f"{key}={value}")
    path.write_text("\n".join(output) + "\n", encoding="utf-8")


def _backup_file(path: Path, *, root: str | Path | None = None) -> Path | None:
    """Copy ``path`` into ``data/runtime/backups/`` (gitignored via /data/)."""

    if not path.is_file():
        return None
    backup_dir = runtime_paths.runtime_dir(root) / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup = backup_dir / f"{path.name}.bak"
    shutil.copy2(path, backup)
    return backup


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ValueError(f"Invalid YAML in {path}: {exc}") from exc
    return payload if isinstance(payload, dict) else {}


def _load_env_file(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    result: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        result[key.strip()] = _parse_dotenv_value(value)
    return result


def _parse_dotenv_value(raw: str) -> str:
    value = raw.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        return value[1:-1]
    return value


def _default_prompt(message: str, default: str) -> str:
    suffix = f" [{default}]" if default else ""
    return input(f"{message}{suffix}: ")


def _default_secret_prompt(message: str, _default: str) -> str:
    """Prompt without echoing input (API keys / tokens)."""

    try:
        return getpass.getpass(f"{message}: ")
    except (EOFError, KeyboardInterrupt):
        return ""


def _default_confirm(message: str, default: bool) -> bool:
    hint = "Y/n" if default else "y/N"
    raw = input(f"{message} [{hint}]: ").strip().lower()
    if not raw:
        return default
    return raw in {"y", "yes", "true", "1"}


_SECRET_LINE_RE = re.compile(
    r"(OPENAI_API_KEY|TELEGRAM_BOT_TOKEN|TELEGRAM_CHAT_ID|"
    r"LINKEDIN_EMAIL_IMAP_PASSWORD)=([^\s]+)"
)


def redact_setup_text(text: str) -> str:
    """Redact secret assignments in setup/doctor output."""

    def _replace(match: re.Match[str]) -> str:
        return f"{match.group(1)}=***"

    return _SECRET_LINE_RE.sub(_replace, text)
