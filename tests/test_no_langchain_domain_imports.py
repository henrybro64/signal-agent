"""Guard: LangChain must not leak into domain packages."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOMAIN_GLOBS = (
    "app/core/**/*.py",
    "app/sources/**/*.py",
    "app/scorers/**/*.py",
    "app/notifiers/**/*.py",
    "app/tools/**/*.py",
    "app/tasks/job_search.py",
    "app/tasks/tw_stock_movers.py",
    "app/tasks/stock_watchlist.py",
    "app/tasks/tw_market_assistant.py",
    "app/tasks/fetch.py",
    "app/tasks/dummy.py",
)
_IMPORT_RE = re.compile(
    r"^\s*(?:from|import)\s+(langchain(?:_[a-z0-9]+)?|langgraph)\b",
    re.MULTILINE,
)


def test_domain_modules_do_not_import_langchain() -> None:
    offenders: list[str] = []
    for pattern in DOMAIN_GLOBS:
        for path in ROOT.glob(pattern):
            text = path.read_text(encoding="utf-8")
            if _IMPORT_RE.search(text):
                offenders.append(str(path.relative_to(ROOT)))
    assert not offenders, f"LangChain leaked into domain modules: {offenders}"
