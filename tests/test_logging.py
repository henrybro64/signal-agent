import json
import logging

import pytest

from app.core.logging import JsonLogFormatter, configure_logging, log_event


def test_json_log_formatter_includes_structured_fields_and_stack_trace() -> None:
    formatter = JsonLogFormatter()

    try:
        raise RuntimeError("boom")
    except RuntimeError as exc:
        record = logging.LogRecord(
            name="signal_agent.tasks.fetch",
            level=logging.ERROR,
            pathname=__file__,
            lineno=1,
            msg="Source run failed",
            args=(),
            exc_info=(type(exc), exc, exc.__traceback__),
        )
        record.event = "source_run_failed"
        record.task_name = "fetch"
        record.source_name = "dummy"
        record.run_id = 7

    payload = json.loads(formatter.format(record))

    assert payload["level"] == "error"
    assert payload["event"] == "source_run_failed"
    assert payload["task_name"] == "fetch"
    assert payload["source_name"] == "dummy"
    assert payload["run_id"] == 7
    assert "RuntimeError: boom" in payload["stack_trace"]


def test_log_event_omits_none_fields(capsys: pytest.CaptureFixture[str]) -> None:
    logger = configure_logging()

    log_event(
        logger,
        event="task_completed",
        message="Fetch task completed",
        task_name="fetch",
        fetched_count=2,
        notified_count=None,
    )

    captured = capsys.readouterr()
    payload = json.loads(captured.out.strip())

    assert payload["event"] == "task_completed"
    assert payload["fetched_count"] == 2
    assert "notified_count" not in payload
