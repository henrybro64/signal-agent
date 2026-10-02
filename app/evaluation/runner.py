"""Offline evaluation runner (#66)."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
from pathlib import Path
from typing import Any

import yaml

from app.llm import FakeLLMProvider, LLMProviderError, create_llm_provider
from app.tools import ToolContext, ToolRegistry, build_tool_registry, builtin_tools
from app.tools.models import ToolResult
from app.workflows import JobResearchState, run_job_research_python

from .models import (
    EvalCase,
    EvalCaseResult,
    EvalCheckResult,
    EvalExpectation,
    EvalReport,
)
from .report import format_report_text, write_report_json
from .trace import TraceCollector

_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_CASES_DIR = _ROOT / "evals" / "cases"
_LIVE_FLAG = "SIGNAL_AGENT_LIVE_EVAL"


def _env_flag(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


def load_eval_cases(
    *,
    suite: str | None = None,
    cases_dir: Path | None = None,
) -> list[EvalCase]:
    directory = cases_dir or _DEFAULT_CASES_DIR
    cases: list[EvalCase] = []
    for path in sorted(directory.glob("*.yaml")):
        payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if not isinstance(payload, dict):
            continue
        case_suite = str(payload.get("suite") or "core")
        if suite and case_suite != suite:
            continue
        expectations_raw = payload.get("expectations") or {}
        if not isinstance(expectations_raw, dict):
            expectations_raw = {}
        cases.append(
            EvalCase(
                id=str(payload.get("id") or path.stem),
                suite=case_suite,
                description=str(payload.get("description") or ""),
                version=int(payload.get("version") or 1),
                kind=str(payload.get("kind") or "job_research"),
                inputs=dict(payload.get("inputs") or {}),
                expectations=EvalExpectation(
                    final_status_prefix=expectations_raw.get("final_status_prefix"),
                    required_tools=list(
                        expectations_raw.get("required_tools") or []
                    ),
                    prohibited_substrings=list(
                        expectations_raw.get("prohibited_substrings") or []
                    ),
                    required_substrings=list(
                        expectations_raw.get("required_substrings") or []
                    ),
                    max_tool_calls=expectations_raw.get("max_tool_calls"),
                    max_llm_calls=expectations_raw.get("max_llm_calls"),
                    require_citations=bool(
                        expectations_raw.get("require_citations") or False
                    ),
                    min_filtered=expectations_raw.get("min_filtered"),
                    expected_recommendation=expectations_raw.get(
                        "expected_recommendation"
                    ),
                    min_score=expectations_raw.get("min_score"),
                    max_score=expectations_raw.get("max_score"),
                ),
            )
        )
    return cases


def run_suite(
    suite: str = "core",
    *,
    cases_dir: Path | None = None,
    traces_dir: Path | None = None,
    live: bool | None = None,
) -> EvalReport:
    """Run an offline suite.

    Paid/live providers require ``SIGNAL_AGENT_LIVE_EVAL=1`` *and*
    ``inputs.allow_live: true`` on the case. Standard CI never sets the flag.
    """

    use_live = _env_flag(_LIVE_FLAG) if live is None else live
    cases = load_eval_cases(suite=suite, cases_dir=cases_dir)
    results = [
        _run_case(case, traces_dir=traces_dir, live=use_live) for case in cases
    ]
    passed = all(result.passed for result in results) if results else True
    return EvalReport(suite=suite, passed=passed, results=results)


def _run_case(
    case: EvalCase,
    *,
    traces_dir: Path | None,
    live: bool,
) -> EvalCaseResult:
    collector = TraceCollector(
        suite=case.suite,
        case_id=case.id,
        secrets=("sk-test-secret", "sk-secret-value"),
    )
    if case.kind == "tools":
        result = _run_tools_case(case, collector)
    elif case.kind == "job_scoring":
        result = _run_job_scoring_case(case, collector)
    else:
        result = _run_job_research_case(case, collector, live=live)

    if traces_dir is not None:
        traces_dir.mkdir(parents=True, exist_ok=True)
        target = traces_dir / f"{case.id}.json"
        target.write_text(
            json.dumps(collector.trace.to_dict(), indent=2, ensure_ascii=False)
            + "\n",
            encoding="utf-8",
        )
    return result


def _run_tools_case(case: EvalCase, collector: TraceCollector) -> EvalCaseResult:
    registry = _registry_for_case(case)
    tool_name = str(case.inputs.get("tool") or "search_jobs")
    arguments = dict(case.inputs.get("arguments") or {})
    with collector.span("tool", tool_name) as span:
        try:
            context = ToolContext(
                fetch_jobs=lambda **_: list(case.inputs.get("jobs") or []),
                fetch_movers=lambda **_: list(case.inputs.get("movers") or []),
                query_runs=lambda **_: list(case.inputs.get("runs") or []),
                telegram_enabled=False,
                secrets_to_redact=("sk-test-secret",),
            )
            result = registry.invoke(tool_name, arguments, context=context)
        except Exception as exc:  # noqa: BLE001
            result = ToolResult(
                ok=False,
                error=str(exc),
                metadata={"code": "provider_error"},
            )
        span.metadata["ok"] = result.ok
        span.status = "ok" if result.ok else "error"

    checks = _score_expectations(
        case.expectations,
        digest=str(result.data),
        status="ok" if result.ok else "error",
        tool_calls=1,
        llm_calls=0,
        used_tools=[tool_name],
        evidence=[],
    )
    if case.inputs.get("expect_ok") is False:
        checks.append(
            EvalCheckResult(
                name="expect_tool_rejected",
                passed=not result.ok,
                detail="tool should be rejected",
            )
        )
    elif case.inputs.get("expect_ok") is True:
        checks.append(
            EvalCheckResult(
                name="expect_tool_ok",
                passed=result.ok,
                detail=result.error or "",
            )
        )
    return EvalCaseResult(
        case_id=case.id,
        suite=case.suite,
        passed=all(check.passed for check in checks),
        checks=checks,
        trace_id=collector.trace.trace_id,
        summary={"ok": result.ok, "tool": tool_name},
    )


def _run_job_scoring_case(
    case: EvalCase,
    collector: TraceCollector,
) -> EvalCaseResult:
    from datetime import UTC, datetime

    from app.core.models import SignalItem
    from app.scorers import JobRuleScorer

    raw = dict(case.inputs.get("job") or {})
    item = SignalItem(
        source=str(raw.get("source") or "eval"),
        item_type=str(raw.get("item_type") or "job"),
        title=str(raw.get("title") or "Untitled"),
        observed_at=datetime.now(UTC),
        content=str(raw.get("content") or ""),
        metadata=dict(raw.get("metadata") or {}),
    )
    with collector.span("scorer", "job_rule_scorer") as span:
        score = JobRuleScorer().score(item)
        span.metadata["recommendation"] = score.recommendation
        span.metadata["score"] = score.score

    digest = f"{score.recommendation}:{score.score}:{score.reason}"
    checks = _score_expectations(
        case.expectations,
        digest=digest,
        status="completed",
        tool_calls=0,
        llm_calls=0,
        used_tools=[],
        evidence=[],
    )
    if case.expectations.expected_recommendation:
        expected = case.expectations.expected_recommendation
        checks.append(
            EvalCheckResult(
                name="expected_recommendation",
                passed=score.recommendation == expected,
                detail=f"got={score.recommendation}",
            )
        )
    if case.expectations.min_score is not None:
        checks.append(
            EvalCheckResult(
                name="min_score",
                passed=score.score >= case.expectations.min_score,
                detail=f"score={score.score}",
            )
        )
    if case.expectations.max_score is not None:
        checks.append(
            EvalCheckResult(
                name="max_score",
                passed=score.score <= case.expectations.max_score,
                detail=f"score={score.score}",
            )
        )
    return EvalCaseResult(
        case_id=case.id,
        suite=case.suite,
        passed=all(check.passed for check in checks),
        checks=checks,
        trace_id=collector.trace.trace_id,
        summary={
            "recommendation": score.recommendation,
            "score": score.score,
            "reason": score.reason,
        },
    )


def _run_job_research_case(
    case: EvalCase,
    collector: TraceCollector,
    *,
    live: bool,
) -> EvalCaseResult:
    jobs = list(case.inputs.get("jobs") or [])
    provider = _provider_for_case(case, live=live)

    work_dir = Path(tempfile.mkdtemp(prefix="signal-agent-eval-"))
    db_path = work_dir / "eval.sqlite3"
    _seed_memory_docs(case, db_path=db_path, work_dir=work_dir)

    registry = _registry_for_case(case)
    context = ToolContext(
        fetch_jobs=lambda **_: jobs,
        telegram_enabled=False,
        db_path=db_path,
        secrets_to_redact=("sk-test-secret",),
    )
    task_run_id = case.inputs.get("task_run_id")
    collector.trace.task_run_id = (
        int(task_run_id) if task_run_id is not None else None
    )
    with collector.span("workflow", "job_research") as span:
        final = run_job_research_python(
            JobResearchState(
                profile_name=str(case.inputs.get("profile_name") or "default"),
                task_run_id=collector.trace.task_run_id,
                max_steps=int(case.inputs.get("max_steps") or 12),
                max_tool_calls=int(case.inputs.get("max_tool_calls") or 6),
                max_llm_calls=int(case.inputs.get("max_llm_calls") or 3),
                memory_namespace=str(
                    case.inputs.get("memory_namespace") or "job_research"
                ),
            ),
            registry=registry,
            context=context,
            provider=provider,
        )
        span.metadata["status"] = final.status
        span.metadata["tool_calls"] = final.tool_calls
        span.metadata["tools_used"] = list(final.tools_used)
        span.metadata["llm_calls"] = final.llm_calls
        if collector.trace.task_run_id is None and final.task_run_id is not None:
            collector.trace.task_run_id = final.task_run_id

    collector.record_text(kind="digest", name="final", text=final.digest_text)
    checks = _score_expectations(
        case.expectations,
        digest=final.digest_text,
        status=final.status,
        tool_calls=final.tool_calls,
        llm_calls=final.llm_calls,
        used_tools=list(final.tools_used),
        evidence=final.evidence,
    )
    return EvalCaseResult(
        case_id=case.id,
        suite=case.suite,
        passed=all(check.passed for check in checks),
        checks=checks,
        trace_id=collector.trace.trace_id,
        summary=final.to_summary(),
    )


def _provider_for_case(case: EvalCase, *, live: bool) -> Any:
    allow_live = bool(case.inputs.get("allow_live"))
    if live and allow_live:
        try:
            from app.core.secrets import load_env_settings

            env = load_env_settings()
            return create_llm_provider(
                provider="openai",
                api_key=env.openai_api_key,
                model=str(case.inputs.get("live_model") or "gpt-4o-mini"),
            )
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(
                "Live eval requested (SIGNAL_AGENT_LIVE_EVAL + allow_live) "
                f"but provider setup failed: {exc}"
            ) from exc

    if live and not allow_live:
        # Live flag set globally but this case stays offline.
        pass

    llm_mode = str(case.inputs.get("llm_mode") or "ok")
    if llm_mode == "absent":
        return None
    if llm_mode == "error":
        return FakeLLMProvider(responses=[LLMProviderError("boom")])
    return FakeLLMProvider(
        responses=[str(case.inputs.get("llm_text") or "Eval note")],
        validate_structured_json=False,
    )


def _seed_memory_docs(
    case: EvalCase,
    *,
    db_path: Path,
    work_dir: Path,
) -> None:
    docs = case.inputs.get("memory_docs") or []
    if not isinstance(docs, list) or not docs:
        return
    from app.memory import SqliteMemoryStore, ingest_document

    store = SqliteMemoryStore(db_path)
    namespace = str(case.inputs.get("memory_namespace") or "job_research")
    for index, raw in enumerate(docs):
        if not isinstance(raw, dict):
            continue
        filename = str(raw.get("filename") or f"note-{index}.md")
        content = str(raw.get("content") or "")
        path = work_dir / filename
        path.write_text(content, encoding="utf-8")
        ingest_document(store, namespace=namespace, path=path)


def _registry_for_case(case: EvalCase) -> ToolRegistry:
    allowlist = case.inputs.get("allowlist")
    if isinstance(allowlist, list) and allowlist:
        return ToolRegistry(
            builtin_tools(),
            allowlist=[str(name) for name in allowlist],
        )
    return build_tool_registry()


def _score_expectations(
    expectations: EvalExpectation,
    *,
    digest: str,
    status: str,
    tool_calls: int,
    llm_calls: int,
    used_tools: list[str],
    evidence: list[dict[str, Any]],
) -> list[EvalCheckResult]:
    checks: list[EvalCheckResult] = []
    if expectations.final_status_prefix:
        checks.append(
            EvalCheckResult(
                name="final_status_prefix",
                passed=status.startswith(expectations.final_status_prefix),
                detail=f"status={status}",
            )
        )
    for tool_name in expectations.required_tools:
        checks.append(
            EvalCheckResult(
                name=f"required_tool:{tool_name}",
                passed=tool_name in used_tools,
                detail=f"used={used_tools}",
            )
        )
    for needle in expectations.required_substrings:
        checks.append(
            EvalCheckResult(
                name=f"required_substring:{needle}",
                passed=needle in digest,
                detail="missing from digest" if needle not in digest else "",
            )
        )
    for needle in expectations.prohibited_substrings:
        checks.append(
            EvalCheckResult(
                name=f"prohibited_substring:{needle}",
                passed=needle not in digest,
                detail="found in digest" if needle in digest else "",
            )
        )
    if expectations.max_tool_calls is not None:
        checks.append(
            EvalCheckResult(
                name="max_tool_calls",
                passed=tool_calls <= expectations.max_tool_calls,
                detail=f"tool_calls={tool_calls}",
            )
        )
    if expectations.max_llm_calls is not None:
        checks.append(
            EvalCheckResult(
                name="max_llm_calls",
                passed=llm_calls <= expectations.max_llm_calls,
                detail=f"llm_calls={llm_calls}",
            )
        )
    if expectations.min_filtered is not None:
        match = re.search(r"Filtered matches:\s*(\d+)", digest)
        count = int(match.group(1)) if match else -1
        checks.append(
            EvalCheckResult(
                name="min_filtered",
                passed=count >= expectations.min_filtered,
                detail=f"filtered={count}",
            )
        )
    if expectations.require_citations:
        has_citation = any(
            isinstance(item, dict) and item.get("citation") for item in evidence
        ) or "#" in digest
        checks.append(
            EvalCheckResult(
                name="require_citations",
                passed=has_citation,
                detail="no citations found",
            )
        )
    for banned in ("apply now", "submit application", "buy shares", "sell shares"):
        checks.append(
            EvalCheckResult(
                name=f"safety:{banned}",
                passed=banned not in digest.lower(),
                detail="prohibited claim present",
            )
        )
    return checks


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="signal-agent-eval")
    parser.add_argument("--suite", default="core")
    parser.add_argument("--cases-dir", default=str(_DEFAULT_CASES_DIR))
    parser.add_argument("--json-out", default="")
    parser.add_argument(
        "--traces-dir",
        default="",
        help="Write redacted per-case JSON traces to this directory",
    )
    args = parser.parse_args(argv)

    traces_dir = Path(args.traces_dir) if args.traces_dir else None
    if traces_dir is None and args.json_out:
        traces_dir = Path(args.json_out).resolve().parent / "traces"

    report = run_suite(
        args.suite,
        cases_dir=Path(args.cases_dir),
        traces_dir=traces_dir,
    )
    print(format_report_text(report))
    if args.json_out:
        write_report_json(report, args.json_out)
    return 0 if report.passed else 1


if __name__ == "__main__":
    sys.exit(main())
