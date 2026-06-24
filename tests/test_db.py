import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.core import (
    SignalItem,
    finish_signal_run,
    init_db,
    start_signal_run,
    update_signal_evaluation,
    update_signal_status,
    upsert_signal,
)

OBSERVED_AT = datetime(2026, 6, 15, 12, 0, tzinfo=UTC)


def test_init_db_creates_required_tables(tmp_path: Path) -> None:
    db_path = tmp_path / "signals.sqlite3"

    init_db(db_path)

    with sqlite3.connect(db_path) as connection:
        rows = connection.execute(
            """
            SELECT name
            FROM sqlite_master
            WHERE type = 'table'
            ORDER BY name
            """
        ).fetchall()

    assert {row[0] for row in rows} >= {"signal_runs", "signals"}


def test_init_db_matches_schema_contract(tmp_path: Path) -> None:
    db_path = tmp_path / "signals.sqlite3"

    init_db(db_path)

    signal_columns = _table_columns(db_path, "signals")

    assert list(signal_columns) == [
        "id",
        "source",
        "item_type",
        "external_id",
        "title",
        "url",
        "content",
        "content_hash",
        "metadata_json",
        "first_seen_at",
        "last_seen_at",
        "status",
        "score",
        "reason",
    ]
    assert signal_columns["source"]["type"] == "TEXT"
    assert signal_columns["source"]["notnull"] is True
    assert signal_columns["item_type"]["type"] == "TEXT"
    assert signal_columns["item_type"]["notnull"] is True
    assert signal_columns["external_id"]["type"] == "TEXT"
    assert signal_columns["title"]["type"] == "TEXT"
    assert signal_columns["title"]["notnull"] is True
    assert signal_columns["url"]["type"] == "TEXT"
    assert signal_columns["content"]["type"] == "TEXT"
    assert signal_columns["content_hash"]["type"] == "TEXT"
    assert signal_columns["metadata_json"]["type"] == "TEXT"
    assert signal_columns["metadata_json"]["notnull"] is True
    assert signal_columns["metadata_json"]["default"] == "'{}'"
    assert signal_columns["first_seen_at"]["type"] == "TEXT"
    assert signal_columns["first_seen_at"]["notnull"] is True
    assert signal_columns["last_seen_at"]["type"] == "TEXT"
    assert signal_columns["last_seen_at"]["notnull"] is True
    assert signal_columns["status"]["type"] == "TEXT"
    assert signal_columns["status"]["notnull"] is True
    assert signal_columns["score"]["type"] == "REAL"
    assert signal_columns["reason"]["type"] == "TEXT"

    signal_indexes = _table_indexes(db_path, "signals")

    assert signal_indexes == {
        "idx_signals_content_hash_unique": ["content_hash"],
        "idx_signals_source_external_id_unique": ["source", "external_id"],
        "idx_signals_url_unique": ["url"],
    }

    signal_run_columns = _table_columns(db_path, "signal_runs")

    assert list(signal_run_columns) == [
        "id",
        "source",
        "started_at",
        "finished_at",
        "status",
        "fetched_count",
        "inserted_count",
        "updated_count",
        "error",
    ]
    assert signal_run_columns["source"]["type"] == "TEXT"
    assert signal_run_columns["source"]["notnull"] is True
    assert signal_run_columns["started_at"]["type"] == "TEXT"
    assert signal_run_columns["started_at"]["notnull"] is True
    assert signal_run_columns["finished_at"]["type"] == "TEXT"
    assert signal_run_columns["status"]["type"] == "TEXT"
    assert signal_run_columns["status"]["notnull"] is True
    assert signal_run_columns["fetched_count"]["type"] == "INTEGER"
    assert signal_run_columns["fetched_count"]["notnull"] is True
    assert signal_run_columns["fetched_count"]["default"] == "0"
    assert signal_run_columns["inserted_count"]["type"] == "INTEGER"
    assert signal_run_columns["inserted_count"]["notnull"] is True
    assert signal_run_columns["inserted_count"]["default"] == "0"
    assert signal_run_columns["updated_count"]["type"] == "INTEGER"
    assert signal_run_columns["updated_count"]["notnull"] is True
    assert signal_run_columns["updated_count"]["default"] == "0"
    assert signal_run_columns["error"]["type"] == "TEXT"


def test_upsert_signal_inserts_new_signal(tmp_path: Path) -> None:
    db_path = tmp_path / "signals.sqlite3"
    item = SignalItem(
        source="linkedin",
        item_type="job",
        title="Backend Engineer",
        observed_at=OBSERVED_AT,
        url="https://example.com/jobs/1",
        external_id="job-1",
        content="Design and ship backend systems.",
        metadata={"company": "Example Labs", "location": "Taipei"},
    )

    init_db(db_path)
    result = upsert_signal(item, db_path=db_path)

    row = _fetch_signal(db_path, result.signal_id)

    assert result.action == "inserted"
    assert result.matched_on is None
    assert row["source"] == "linkedin"
    assert row["title"] == "Backend Engineer"
    assert row["content"] == "Design and ship backend systems."
    assert row["status"] == "new"
    assert row["first_seen_at"] == "2026-06-15T12:00:00Z"
    assert row["last_seen_at"] == "2026-06-15T12:00:00Z"
    assert json.loads(row["metadata_json"]) == {
        "company": "Example Labs",
        "location": "Taipei",
    }


def test_upsert_signal_dedups_by_url_and_refreshes_timestamps(tmp_path: Path) -> None:
    db_path = tmp_path / "signals.sqlite3"
    first = SignalItem(
        source="rss",
        item_type="news",
        title="AI round-up",
        observed_at=OBSERVED_AT,
        url="https://example.com/posts/ai-round-up",
        content="Morning AI digest.",
    )
    second = SignalItem(
        source="rss",
        item_type="news",
        title="AI round-up updated",
        observed_at=datetime(2026, 6, 16, 8, 30, tzinfo=UTC),
        url="https://example.com/posts/ai-round-up",
        content="Morning AI digest with late edits.",
    )

    init_db(db_path)
    upsert_signal(first, db_path=db_path)
    result = upsert_signal(second, db_path=db_path)

    row = _fetch_signal(db_path, result.signal_id)

    assert result.action == "updated"
    assert result.matched_on == "url"
    assert _count_signals(db_path) == 1
    assert row["title"] == "AI round-up updated"
    assert row["content"] == "Morning AI digest with late edits."
    assert row["first_seen_at"] == "2026-06-15T12:00:00Z"
    assert row["last_seen_at"] == "2026-06-16T08:30:00Z"


def test_upsert_signal_dedups_by_source_and_external_id(tmp_path: Path) -> None:
    db_path = tmp_path / "signals.sqlite3"
    first = SignalItem(
        source="linkedin",
        item_type="job",
        title="Platform Engineer",
        observed_at=OBSERVED_AT,
        external_id="job-42",
        url="https://example.com/jobs/old",
        content="First version.",
    )
    second = SignalItem(
        source="linkedin",
        item_type="job",
        title="Platform Engineer updated",
        observed_at=datetime(2026, 6, 16, 9, 0, tzinfo=UTC),
        external_id="job-42",
        url="https://example.com/jobs/new",
        content="Second version.",
    )

    init_db(db_path)
    upsert_signal(first, db_path=db_path)
    result = upsert_signal(second, db_path=db_path)

    row = _fetch_signal(db_path, result.signal_id)

    assert result.action == "updated"
    assert result.matched_on == "source_external_id"
    assert _count_signals(db_path) == 1
    assert row["title"] == "Platform Engineer updated"
    assert row["url"] == "https://example.com/jobs/new"
    assert row["last_seen_at"] == "2026-06-16T09:00:00Z"


def test_upsert_signal_dedups_by_content_hash(tmp_path: Path) -> None:
    db_path = tmp_path / "signals.sqlite3"
    first = SignalItem(
        source="manual",
        item_type="job",
        title="Agent Engineer",
        observed_at=OBSERVED_AT,
        content="Build reliable agent workflows.",
    )
    second = SignalItem(
        source="another-source",
        item_type="job",
        title="Agent Engineer",
        observed_at=datetime(2026, 6, 16, 11, 0, tzinfo=UTC),
        content="Build reliable agent workflows.",
    )

    init_db(db_path)
    upsert_signal(first, db_path=db_path)
    result = upsert_signal(second, db_path=db_path)

    row = _fetch_signal(db_path, result.signal_id)

    assert result.action == "updated"
    assert result.matched_on == "content_hash"
    assert _count_signals(db_path) == 1
    assert row["source"] == "manual"
    assert row["last_seen_at"] == "2026-06-16T11:00:00Z"


def test_upsert_stock_mover_snapshots_preserve_history_rows(tmp_path: Path) -> None:
    db_path = tmp_path / "signals.sqlite3"
    first = SignalItem(
        source="twse",
        item_type="tw_stock_mover",
        title="TSMC gains 3.10%",
        observed_at=OBSERVED_AT,
        external_id="twse-20260615-090000-2330",
        url=(
            "https://www.twse.com.tw/zh/page/trading/exchange/STOCK_DAY.html"
            "?date=20260615&stockNo=2330&snapshot=090000"
        ),
        content="TWSE 2330 last 1000.00, prev close 970.00.",
    )
    second = SignalItem(
        source="twse",
        item_type="tw_stock_mover",
        title="TSMC gains 3.10%",
        observed_at=datetime(2026, 6, 15, 12, 10, tzinfo=UTC),
        external_id="twse-20260615-091000-2330",
        url=(
            "https://www.twse.com.tw/zh/page/trading/exchange/STOCK_DAY.html"
            "?date=20260615&stockNo=2330&snapshot=091000"
        ),
        content="TWSE 2330 last 1000.00, prev close 970.00.",
    )

    init_db(db_path)
    first_result = upsert_signal(first, db_path=db_path)
    second_result = upsert_signal(second, db_path=db_path)

    assert first_result.action == "inserted"
    assert second_result.action == "inserted"
    assert second_result.matched_on is None
    assert _count_signals(db_path) == 2


def test_update_signal_status_persists_new_value(tmp_path: Path) -> None:
    db_path = tmp_path / "signals.sqlite3"
    item = SignalItem(
        source="manual",
        item_type="job",
        title="Signal to review",
        observed_at=OBSERVED_AT,
    )

    init_db(db_path)
    result = upsert_signal(item, db_path=db_path)
    update_signal_status(result.signal_id, "reviewed", db_path=db_path)

    row = _fetch_signal(db_path, result.signal_id)

    assert row["status"] == "reviewed"


def test_update_signal_status_rejects_invalid_value(tmp_path: Path) -> None:
    db_path = tmp_path / "signals.sqlite3"
    item = SignalItem(
        source="manual",
        item_type="job",
        title="Signal to review",
        observed_at=OBSERVED_AT,
    )

    init_db(db_path)
    result = upsert_signal(item, db_path=db_path)

    with pytest.raises(ValueError, match="Invalid signal status: archived"):
        update_signal_status(result.signal_id, "archived", db_path=db_path)  # type: ignore[arg-type]


def test_update_signal_evaluation_persists_score_reason_and_status(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "signals.sqlite3"
    item = SignalItem(
        source="manual",
        item_type="job",
        title="Signal to score",
        observed_at=OBSERVED_AT,
    )

    init_db(db_path)
    result = upsert_signal(item, db_path=db_path)
    update_signal_evaluation(
        result.signal_id,
        score=82,
        reason="strong match (82/100): positives: python; concerns: none.",
        status="reviewed",
        db_path=db_path,
    )

    row = _fetch_signal(db_path, result.signal_id)

    assert row["score"] == 82
    assert row["reason"] == (
        "strong match (82/100): positives: python; concerns: none."
    )
    assert row["status"] == "reviewed"


def test_signal_run_lifecycle_persists_counts(tmp_path: Path) -> None:
    db_path = tmp_path / "signals.sqlite3"

    init_db(db_path)
    run_id = start_signal_run("linkedin", db_path=db_path)
    finish_signal_run(
        run_id,
        status="completed",
        fetched_count=3,
        inserted_count=2,
        updated_count=1,
        db_path=db_path,
    )

    row = _fetch_signal_run(db_path, run_id)

    assert row["source"] == "linkedin"
    assert row["status"] == "completed"
    assert row["fetched_count"] == 3
    assert row["inserted_count"] == 2
    assert row["updated_count"] == 1
    assert row["finished_at"] is not None


def _fetch_signal_run(db_path: Path, run_id: int) -> sqlite3.Row:
    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute(
            "SELECT * FROM signal_runs WHERE id = ?",
            (run_id,),
        ).fetchone()

    assert row is not None
    return row


def _fetch_signal(db_path: Path, signal_id: int) -> sqlite3.Row:
    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute(
            "SELECT * FROM signals WHERE id = ?",
            (signal_id,),
        ).fetchone()

    assert row is not None
    return row


def _count_signals(db_path: Path) -> int:
    with sqlite3.connect(db_path) as connection:
        row = connection.execute("SELECT COUNT(*) FROM signals").fetchone()

    assert row is not None
    return int(row[0])


def _table_columns(
    db_path: Path,
    table_name: str,
) -> dict[str, dict[str, str | bool | int | None]]:
    with sqlite3.connect(db_path) as connection:
        rows = connection.execute(
            f"PRAGMA table_info({table_name})"
        ).fetchall()

    return {
        row[1]: {
            "type": row[2],
            "notnull": bool(row[3]),
            "default": row[4],
            "pk": row[5],
        }
        for row in rows
    }


def _table_indexes(db_path: Path, table_name: str) -> dict[str, list[str]]:
    with sqlite3.connect(db_path) as connection:
        index_rows = connection.execute(
            f"PRAGMA index_list({table_name})"
        ).fetchall()

        indexes: dict[str, list[str]] = {}
        for row in index_rows:
            index_name = row[1]
            if not index_name.startswith("idx_"):
                continue
            index_columns = connection.execute(
                f"PRAGMA index_info({index_name})"
            ).fetchall()
            indexes[index_name] = [column[2] for column in index_columns]

    return dict(sorted(indexes.items()))
