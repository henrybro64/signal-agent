"""Human and machine-readable evaluation reports."""

from __future__ import annotations

import json
from pathlib import Path

from .models import EvalReport


def format_report_text(report: EvalReport) -> str:
    lines = [
        f"suite={report.suite}",
        f"passed={report.passed}",
        f"cases={len(report.results)}",
    ]
    for result in report.results:
        mark = "PASS" if result.passed else "FAIL"
        lines.append(f"- [{mark}] {result.case_id}")
        for check in result.checks:
            if not check.passed:
                lines.append(f"    x {check.name}: {check.detail}")
    return "\n".join(lines)


def write_report_json(report: EvalReport, path: str | Path) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(report.to_dict(), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
