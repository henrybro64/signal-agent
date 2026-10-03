"""First-run setup and configuration helpers (#81)."""

from .browser import BrowserDetection, detect_browser
from .validate import (
    ValidationIssue,
    ValidationReport,
    format_validation_report,
    validate_local_setup,
)
from .wizard import (
    SetupOptions,
    SetupResult,
    redact_setup_text,
    run_setup,
    show_config_redacted,
)

__all__ = [
    "BrowserDetection",
    "SetupOptions",
    "SetupResult",
    "ValidationIssue",
    "ValidationReport",
    "detect_browser",
    "format_validation_report",
    "redact_setup_text",
    "run_setup",
    "show_config_redacted",
    "validate_local_setup",
]
