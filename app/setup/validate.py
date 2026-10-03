"""Configuration validation for setup/doctor (#81)."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from app.core.config import ConfigError, load_config
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
    except (ConfigError, OSError, ValueError) as exc:
        report.add("config_invalid", str(exc))
        return report

    providers = supported_llm_providers()
    if config.llm.provider not in providers:
        report.add(
            "invalid_provider",
            f"Unsupported llm.provider {config.llm.provider!r}; "
            f"supported: {', '.join(sorted(providers))}",
        )
    if not str(config.llm.model or "").strip():
        report.add("invalid_model", "llm.model must be a non-empty string")

    env = load_env_settings(dotenv_path)
    if config.llm.provider == "openai" and config.scoring.llm_enabled:
        if not has_openai_api_key(env):
            report.add(
                "openai_key_missing",
                "OPENAI_API_KEY required when llm.provider=openai and "
                "scoring.llm_enabled=true",
            )
    if config.llm.provider == "openai" and not has_openai_api_key(env):
        report.add(
            "openai_key_absent",
            "OPENAI_API_KEY not set (ok if you keep scoring.llm_enabled=false "
            "or use provider=fake)",
            severity="warning",
        )

    if config.notifiers.telegram.enabled and not has_telegram_credentials(env):
        report.add(
            "telegram_incomplete",
            "Telegram enabled but TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID missing",
            severity="warning",
        )

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
