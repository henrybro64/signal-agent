"""Tests for SQLite schema versioning and migrations (issue #57)."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.core import (
    MigrationError,
    SignalItem,
    init_db,
    read_schema_version,
    upsert_signal,
)
from app.core.migrations import (
    apply_pending_migrations,
    get_schema_version,
    latest_migration_version,
    list_migration_files,
    migrations_dir,
)

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = ROOT / "sql" / "schema.sql"
INITIAL_MIGRATION_PATH = ROOT / "sql" / "migrations" / "001_initial.sql"

LEGACY_SIGNALS_AND_RUNS_SCHEMA = """
CREATE TABLE signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,
    item_type TEXT NOT NULL,
    external_id TEXT,
    title TEXT NOT NULL,
    url TEXT,
    content TEXT,
    content_hash TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    status TEXT NOT NULL,
    score REAL,
    reason TEXT
);

CREATE TABLE signal_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL,
    fetched_count INTEGER NOT NULL DEFAULT 0,
    inserted_count INTEGER NOT NULL DEFAULT 0,
    updated_count INTEGER NOT NULL DEFAULT 0,
    error TEXT
);
"""


def test_schema_sql_points_at_migrations() -> None:
    text = SCHEMA_PATH.read_text(encoding="utf-8")
    assert "sql/migrations/" in text
    assert "001_initial.sql" in text
    assert "PRAGMA user_version" in text
    assert "CREATE TABLE" not in text
    assert INITIAL_MIGRATION_PATH.is_file()
    assert "CREATE TABLE IF NOT EXISTS signals" in INITIAL_MIGRATION_PATH.read_text(
        encoding="utf-8"
    )


def test_list_migration_files_discovers_contiguous_versions() -> None:
    migrations = list_migration_files()
    assert migrations[0][0] == 1
    assert migrations[0][1].name == "001_initial.sql"
    assert latest_migration_version() == migrations[-1][0]


def test_init_db_sets_schema_version_on_new_database(tmp_path: Path) -> None:
    db_path = tmp_path / "signals.sqlite3"

    init_db(db_path)

    assert read_schema_version(db_path) == latest_migration_version()
    with sqlite3.connect(db_path) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
    assert {"signals", "signal_runs", "task_runs"} <= tables


def test_init_db_is_idempotent(tmp_path: Path) -> None:
    db_path = tmp_path / "signals.sqlite3"

    init_db(db_path)
    first_version = read_schema_version(db_path)
    init_db(db_path)

    assert read_schema_version(db_path) == first_version == latest_migration_version()


def test_init_db_upgrades_legacy_unversioned_database(tmp_path: Path) -> None:
    db_path = tmp_path / "legacy.sqlite3"
    with sqlite3.connect(db_path) as connection:
        connection.executescript(LEGACY_SIGNALS_AND_RUNS_SCHEMA)
        connection.execute(
            """
            INSERT INTO signals (
                source, item_type, title, url, content_hash, metadata_json,
                first_seen_at, last_seen_at, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "dummy",
                "job",
                "Legacy job",
                "https://example.com/legacy-job",
                "legacy-hash-1",
                "{}",
                "2026-01-01T00:00:00+00:00",
                "2026-01-01T00:00:00+00:00",
                "new",
            ),
        )
        connection.commit()
        assert get_schema_version(connection) == 0
        assert "task_runs" not in {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }

    init_db(db_path)

    assert read_schema_version(db_path) == 1
    with sqlite3.connect(db_path) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        row = connection.execute(
            "SELECT title, url FROM signals WHERE url = ?",
            ("https://example.com/legacy-job",),
        ).fetchone()

    assert "task_runs" in tables
    assert row == ("Legacy job", "https://example.com/legacy-job")

    # Existing rows remain usable through the public DB API after upgrade.
    init_db(db_path)
    result = upsert_signal(
        SignalItem(
            source="dummy",
            item_type="job",
            title="Legacy job refreshed",
            observed_at=datetime(2026, 1, 2, tzinfo=UTC),
            url="https://example.com/legacy-job",
        ),
        db_path=db_path,
    )
    assert result.action == "updated"


def test_failed_migration_does_not_advance_schema_version(tmp_path: Path) -> None:
    migrations_path = tmp_path / "migrations"
    migrations_path.mkdir()
    (migrations_path / "001_ok.sql").write_text(
        "CREATE TABLE IF NOT EXISTS demo (id INTEGER PRIMARY KEY);\n",
        encoding="utf-8",
    )
    (migrations_path / "002_broken.sql").write_text(
        "CREATE TABLE broken (\n    this is not valid SQL\n);\n",
        encoding="utf-8",
    )

    db_path = tmp_path / "migrate.sqlite3"
    with sqlite3.connect(db_path) as connection:
        with pytest.raises(MigrationError, match="002_broken.sql"):
            apply_pending_migrations(
                connection,
                migrations_path=migrations_path,
            )
        # 001 applied and versioned; 002 failed without advancing past 1.
        assert get_schema_version(connection) == 1
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        assert "demo" in tables
        assert "broken" not in tables


def test_list_migration_files_rejects_gaps_and_bad_names(tmp_path: Path) -> None:
    migrations_path = tmp_path / "migrations"
    migrations_path.mkdir()
    (migrations_path / "001_ok.sql").write_text(
        "CREATE TABLE IF NOT EXISTS demo (id INTEGER PRIMARY KEY);\n",
        encoding="utf-8",
    )
    (migrations_path / "003_skip.sql").write_text(
        "CREATE TABLE IF NOT EXISTS other (id INTEGER PRIMARY KEY);\n",
        encoding="utf-8",
    )

    with pytest.raises(MigrationError, match="contiguous"):
        list_migration_files(migrations_path)

    bad_dir = tmp_path / "bad"
    bad_dir.mkdir()
    (bad_dir / "initial.sql").write_text("SELECT 1;\n", encoding="utf-8")
    with pytest.raises(MigrationError, match="Invalid migration filename"):
        list_migration_files(bad_dir)


def test_apply_pending_migrations_rejects_newer_database(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "future.sqlite3"
    with sqlite3.connect(db_path) as connection:
        connection.execute("PRAGMA user_version = 99")
        connection.commit()
        with pytest.raises(MigrationError, match="newer than the supported"):
            apply_pending_migrations(connection)


def test_migrations_dir_points_at_repo_sql_migrations() -> None:
    assert migrations_dir() == ROOT / "sql" / "migrations"
    assert migrations_dir().is_dir()
