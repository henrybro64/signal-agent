"""Configuration validation for setup/doctor (#81)."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from app.core.config import AppConfig, load_config
from app.core.db import init_db, read_schema_version
from app.core.migrations import latest_migration_version
from app.core.secrets import (
    EnvSettings,
    has_telegram_credentials,
    llm_api_keys_from_env,
    load_env_settings,
    resolve_provider_credentials,
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
    env = resolve_provider_credentials(env, config.providers)
    _validate_llm_credentials(report, config, env)
    _validate_telegram(report, config, env)
    _validate_database(report, root)
    _validate_tw_market_calendar(report)

    browser = detect_browser()
    report.add(
        "browser",
        browser.detail,
        severity="info" if browser.ready else "warning",
    )
    _validate_skills(report, root=root, config_path=cfg_path)
    return report


def _validate_tw_market_calendar(report: ValidationReport) -> None:
    """Warn before the packaged holiday calendar runs out (#103)."""

    try:
        from app.core.tw_market_calendar import (
            latest_tw_market_calendar_year,
            tw_market_calendar_coverage_warning,
        )

        warning = tw_market_calendar_coverage_warning()
        if warning:
            report.add("tw_market_calendar", warning, severity="warning")
            return
        latest = latest_tw_market_calendar_year()
        report.add(
            "tw_market_calendar",
            f"Taiwan market holiday calendar covers through {latest}",
            severity="info",
        )
    except Exception as exc:  # noqa: BLE001
        report.add(
            "tw_market_calendar",
            f"Unable to load Taiwan market holiday calendar: {exc}",
            severity="warning",
        )


def _validate_skills(
    report: ValidationReport,
    *,
    root: str | Path | None,
    config_path: Path,
) -> None:
    try:
        from app.skills import (
            list_skills,
            load_builtin_manifests,
        )

        load_builtin_manifests()
        infos = list_skills(root=root, config_path=config_path)
        missing = [
            f"{info.manifest.name}({','.join(info.missing_extras)})"
            for info in infos
            if info.enabled and info.missing_extras
        ]
        if missing:
            report.add(
                "skills_missing_extras",
                "Enabled skills missing extras: " + ", ".join(missing),
                severity="warning",
            )
        else:
            report.add(
                "skills",
                f"{sum(1 for i in infos if i.enabled)}/{len(infos)} skills enabled",
                severity="info",
            )
    except Exception as exc:  # noqa: BLE001
        report.add(
            "skills_invalid",
            f"Skill manifest validation failed: {exc}",
            severity="error",
        )


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


def _validate_llm_credentials(
    report: ValidationReport,
    config: AppConfig,
    env: EnvSettings,
) -> None:
    """Require a credential for the primary and every configured fallback.

    A cross-vendor fallback can only serve traffic with its own key, so
    checking the primary alone would pass a chain that cannot fail over.
    """

    roles: dict[str, str] = {}
    for provider in (
        config.llm.provider,
        *(item.provider for item in config.llm.fallbacks),
    ):
        if provider == "fake":
            continue
        roles.setdefault(
            provider,
            "primary" if provider == config.llm.provider else "fallback",
        )
    keys = llm_api_keys_from_env(env)
    for provider, role in sorted(roles.items()):
        if keys.get(provider):
            continue
        report.add(
            f"{provider}_key_missing",
            f"{_provider_key_env(config, provider)} required for "
            f"{role} llm provider {provider!r} "
            "(needed for chat and optional LLM scoring)",
        )


def _provider_key_env(config: AppConfig, provider: str) -> str:
    credential = getattr(config.providers, provider, None)
    return getattr(credential, "api_key_env", "") or f"{provider.upper()}_API_KEY"


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
