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


def test_empty_suite_and_unknown_kind_fail(tmp_path: Path) -> None:
    import pytest
    import yaml

    from app.evaluation.runner import load_eval_cases, run_suite

    empty_dir = tmp_path / "empty"
    empty_dir.mkdir()
    with pytest.raises(ValueError, match="No eval cases"):
        run_suite("core", cases_dir=empty_dir)

    with pytest.raises(ValueError, match="No eval cases"):
        run_suite("does-not-exist")

    bad = tmp_path / "cases"
    bad.mkdir()
    (bad / "bad.yaml").write_text(
        yaml.safe_dump(
            {
                "id": "bad-kind",
                "suite": "core",
                "kind": "not_a_real_kind",
                "inputs": {},
                "expectations": {},
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="Unknown eval case kind"):
        load_eval_cases(cases_dir=bad)


def test_require_citations_ignores_hash_in_digest() -> None:
    from app.evaluation.models import EvalExpectation
    from app.evaluation.runner import _score_expectations

    checks = _score_expectations(
        EvalExpectation(require_citations=True),
        digest="Looking for a C# Developer role",
        status="completed",
        tool_calls=0,
        llm_calls=0,
        used_tools=[],
        evidence=[],
    )
    citation = next(check for check in checks if check.name == "require_citations")
    assert not citation.passed
