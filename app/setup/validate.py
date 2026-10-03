"""Configuration validation for setup/doctor (#81)."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from app.core.config import load_config
from app.core.db import init_db, read_schema_version
from app.core.migrations import latest_migration_version
from app.core.secrets import (
    has_openai_api_key,
    has_telegram_credentials,
    load_env_settings,
)
from app.llm import supported_llm_providers
from app.runtime import paths as runtime_paths
from app.setup.browser import detect_browser


@dataclass(slots=True)
class ValidationIssue:
    code: str
    message: str
    severity: str = "error"  # error | warning | info


@dataclass(slots=True)
class ValidationReport:
    ok: bool
    issues: list[ValidationIssue] = field(default_factory=list)

    def add(
        self,
        code: str,
        message: str,
        *,
        severity: str = "error",
    ) -> None:
        self.issues.append(
            ValidationIssue(code=code, message=message, severity=severity)
        )
        if severity == "error":
            self.ok = False


def validate_local_setup(
    *,
    root: str | Path | None = None,
    config_path: str | Path | None = None,
    env_path: str | Path | None = None,
) -> ValidationReport:
    """Validate config, secrets, DB, and optional integrations."""

    report = ValidationReport(ok=True)
    cfg_path = Path(config_path or runtime_paths.config_path(root))
    dotenv_path = Path(env_path or runtime_paths.env_path(root))

    if not cfg_path.is_file():
        report.add("config_missing", f"Missing config file: {cfg_path}")
        return report

    try:
        config = load_config(cfg_path)
    except (OSError, ValueError) as exc:
        # ConfigError subclasses ValueError.
        report.add("config_invalid", str(exc))
        return report

    _validate_provider_model(report, config)
    env = load_env_settings(dotenv_path)
    _validate_openai_credentials(report, config, env)
    _validate_telegram(report, config, env)
    _validate_database(report, root)

    browser = detect_browser()
    report.add(
        "browser",
        browser.detail,
        severity="info" if browser.ready else "warning",
    )
    return report


def format_validation_report(report: ValidationReport) -> str:
    lines = [f"Validation: {'OK' if report.ok else 'FAILED'}"]
    for issue in report.issues:
        lines.append(f"- [{issue.severity}] {issue.code}: {issue.message}")
    return "\n".join(lines)


def _validate_provider_model(report: ValidationReport, config: object) -> None:
    providers = supported_llm_providers()
    provider = str(getattr(getattr(config, "llm", None), "provider", "") or "")
    model = str(getattr(getattr(config, "llm", None), "model", "") or "")
    if provider not in providers:
        report.add(
            "invalid_provider",
            f"Unsupported llm.provider {provider!r}; "
            f"supported: {', '.join(sorted(providers))}",
        )
    if not model.strip():
        report.add("invalid_model", "llm.model must be a non-empty string")


def _validate_openai_credentials(
    report: ValidationReport,
    config: object,
    env: object,
) -> None:
    provider = str(getattr(getattr(config, "llm", None), "provider", "") or "")
    if provider != "openai":
        return
    if has_openai_api_key(env):  # type: ignore[arg-type]
        return
    # Chat always constructs the configured provider, so a missing OpenAI key
    # is a readiness failure even when scoring.llm_enabled is false.
    report.add(
        "openai_key_missing",
        "OPENAI_API_KEY required when llm.provider=openai "
        "(needed for chat and optional LLM scoring)",
    )


def _validate_telegram(
    report: ValidationReport,
    config: object,
    env: object,
) -> None:
    notifiers = getattr(config, "notifiers", None)
    telegram = getattr(notifiers, "telegram", None)
    enabled = bool(getattr(telegram, "enabled", False))
    if enabled and not has_telegram_credentials(env):  # type: ignore[arg-type]
        report.add(
            "telegram_incomplete",
            "Telegram enabled but TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID missing",
            severity="warning",
        )


def _validate_database(
    report: ValidationReport,
    root: str | Path | None,
) -> None:
    try:
        db = runtime_paths.db_path(root)
        init_db(db)
        version = read_schema_version(db)
        latest = latest_migration_version()
        if version != latest:
            report.add(
                "schema_mismatch",
                f"DB schema version {version} != latest {latest}",
            )
    except Exception as exc:  # noqa: BLE001
        report.add("db_init_failed", f"SQLite init failed: {exc}")
