"""Offline-first agent evaluation and tracing (#66)."""

from .models import (
    EvalCase,
    EvalCaseResult,
    EvalCheckResult,
    EvalExpectation,
    EvalReport,
)
from .report import format_report_text, write_report_json
from .runner import load_eval_cases, run_suite
from .trace import ExecutionTrace, TraceCollector

__all__ = [
    "EvalCase",
    "EvalCaseResult",
    "EvalCheckResult",
    "EvalExpectation",
    "EvalReport",
    "ExecutionTrace",
    "TraceCollector",
    "format_report_text",
    "load_eval_cases",
    "run_suite",
    "write_report_json",
]
