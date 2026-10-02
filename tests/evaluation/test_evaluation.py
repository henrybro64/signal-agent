"""Tests for offline evaluation and tracing (#66)."""

from __future__ import annotations

import json
from pathlib import Path

from app.evaluation import (
    TraceCollector,
    format_report_text,
    load_eval_cases,
    run_suite,
    write_report_json,
)
from app.evaluation.runner import main as eval_main


def test_load_core_cases() -> None:
    cases = load_eval_cases(suite="core")
    assert cases
    ids = {case.id for case in cases}
    assert "job-research-happy-path" in ids
    assert "tools-telegram-not-allowlisted" in ids
    assert "job-research-rag-citations" in ids
    assert "job-scoring-rule-reject" in ids


def test_run_core_suite_offline(tmp_path: Path) -> None:
    traces_dir = tmp_path / "traces"
    report = run_suite("core", traces_dir=traces_dir)
    text = format_report_text(report)
    assert "suite=core" in text
    assert report.passed, text
    out = tmp_path / "report.json"
    write_report_json(report, out)
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["passed"] is True
    assert payload["case_count"] == len(report.results)
    assert traces_dir.is_dir()
    assert any(traces_dir.glob("*.json"))


def test_eval_cli_exits_zero(tmp_path: Path) -> None:
    assert (
        eval_main(
            [
                "--suite",
                "core",
                "--json-out",
                str(tmp_path / "report.json"),
            ]
        )
        == 0
    )
    assert (tmp_path / "traces").is_dir()


def test_trace_redacts_secrets() -> None:
    collector = TraceCollector(
        suite="core",
        case_id="redact",
        secrets=("sk-secret-value",),
    )
    with collector.span("tool", "search_jobs") as span:
        span.metadata["token"] = "sk-secret-value"
        span.metadata["ok"] = True
    collector.record_text(
        kind="digest",
        name="final",
        text="digest with sk-secret-value inside",
    )
    payload = collector.trace.to_dict()
    blob = json.dumps(payload)
    assert "sk-secret-value" not in blob
    assert payload["events"]


def test_happy_path_records_real_tools_used() -> None:
    report = run_suite("core")
    happy = next(
        row for row in report.results if row.case_id == "job-research-happy-path"
    )
    assert happy.passed
    assert "search_jobs" in (happy.summary.get("tools_used") or [])
