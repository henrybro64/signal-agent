"""Lightweight SQLite schema migrations using PRAGMA user_version."""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path

_MIGRATION_NAME_RE = re.compile(r"^(\d{3})_([a-z0-9_]+)\.sql$")
_PACKAGED_MIGRATIONS = "app.sql"
_PACKAGED_MIGRATIONS_DIR = "migrations"

# Columns required by the current application against schema version 1.
# Used to repair unversioned databases whose CREATE TABLE IF NOT EXISTS
# left an older signals shape in place.
_SIGNALS_V1_COLUMNS: dict[str, str] = {
    "source": "TEXT",
    "item_type": "TEXT",
    "external_id": "TEXT",
    "title": "TEXT",
    "url": "TEXT",
    "content": "TEXT",
    "content_hash": "TEXT",
    "metadata_json": "TEXT NOT NULL DEFAULT '{}'",
    "first_seen_at": "TEXT",
    "last_seen_at": "TEXT",
    "status": "TEXT",
    "score": "REAL",
    "reason": "TEXT",
}


class MigrationError(RuntimeError):
    """Raised when schema migrations cannot be listed or applied safely."""


@dataclass(frozen=True, slots=True)
class Migration:
    """One numbered SQL migration and its script body."""

    version: int
    name: str
    sql: str


def migrations_dir() -> Path:
    """Return the on-disk migrations directory when available.

    Installed wheels expose migrations as package data under ``app.sql``;
    prefer :func:`list_migrations` for runtime application.
    """

    return Path(__file__).resolve().parents[1] / "sql" / "migrations"


def list_migration_files(
    migrations_path: Path | None = None,
) -> list[tuple[int, Path]]:
    """Return sorted ``(version, path)`` pairs from a filesystem directory.

    Prefer :func:`list_migrations` when applying packaged migrations.
    """

    directory = migrations_path if migrations_path is not None else migrations_dir()
    if not directory.is_dir():
        raise MigrationError(f"Migrations directory not found: {directory}")

    loaded = _load_migrations_from_paths(
        [
            path
            for path in sorted(directory.glob("*.sql"))
            if path.is_file()
        ]
    )
    return [(migration.version, directory / migration.name) for migration in loaded]


def list_migrations(migrations_path: Path | None = None) -> list[Migration]:
    """Return sorted migrations from a directory or the packaged resources."""

    if migrations_path is not None:
        directory = Path(migrations_path)
        if not directory.is_dir():
            raise MigrationError(f"Migrations directory not found: {directory}")
        return _load_migrations_from_paths(
            [
                path
                for path in sorted(directory.glob("*.sql"))
                if path.is_file()
            ]
        )

    packaged = files(_PACKAGED_MIGRATIONS).joinpath(_PACKAGED_MIGRATIONS_DIR)
    if not packaged.is_dir():
        raise MigrationError(
            f"Packaged migrations not found: {_PACKAGED_MIGRATIONS}/"
            f"{_PACKAGED_MIGRATIONS_DIR}"
        )
    entries = [
        entry
        for entry in packaged.iterdir()
        if entry.is_file() and entry.name.endswith(".sql")
    ]
    entries.sort(key=lambda entry: entry.name)
    migrations: list[Migration] = []
    for entry in entries:
        version = _parse_migration_version(entry.name)
        migrations.append(
            Migration(
                version=version,
                name=entry.name,
                sql=entry.read_text(encoding="utf-8"),
            )
        )
    return _validate_migration_sequence(migrations)


def get_schema_version(connection: sqlite3.Connection) -> int:
    """Return the database schema version from ``PRAGMA user_version``."""

    row = connection.execute("PRAGMA user_version").fetchone()
    if row is None:
        raise MigrationError("SQLite did not return a user_version value")
    return int(row[0])


def latest_migration_version(migrations_path: Path | None = None) -> int:
    """Return the highest packaged migration version."""

    return list_migrations(migrations_path)[-1].version


def apply_pending_migrations(
    connection: sqlite3.Connection,
    *,
    migrations_path: Path | None = None,
) -> int:
    """Apply pending SQL migrations in order and return the resulting version.

    Each migration runs inside an explicit transaction. The version is bumped
    only after the script and any version-specific repairs succeed. On failure,
    the transaction is rolled back and a clear ``MigrationError`` is raised.
    """

    migrations = list_migrations(migrations_path)
    latest = migrations[-1].version
    current = get_schema_version(connection)

    if current > latest:
        raise MigrationError(
            f"Database schema version {current} is newer than the supported "
            f"version {latest}. Upgrade signal-agent before opening this DB."
        )

    for migration in migrations:
        if migration.version <= current:
            continue
        if migration.version != current + 1:
            raise MigrationError(
                f"Cannot apply migration {migration.name}: database is at "
                f"version {current}, expected to apply version "
                f"{current + 1} next."
            )

        previous_isolation = connection.isolation_level
        connection.isolation_level = None
        try:
            connection.execute("BEGIN")
            try:
                _execute_sql_script(connection, migration.sql)
                if migration.version == 1:
                    _repair_signals_v1_shape(connection)
                connection.execute(f"PRAGMA user_version = {migration.version}")
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise
        except MigrationError:
            raise
        except Exception as exc:
            raise MigrationError(
                f"Failed applying migration {migration.name} while upgrading "
                f"from schema version {current} to {migration.version}: {exc}"
            ) from exc
        finally:
            connection.isolation_level = previous_isolation
        current = migration.version

    return current


def _load_migrations_from_paths(paths: list[Path]) -> list[Migration]:
    migrations = [
        Migration(
            version=_parse_migration_version(path.name),
            name=path.name,
            sql=path.read_text(encoding="utf-8"),
        )
        for path in paths
    ]
    return _validate_migration_sequence(migrations)


def _parse_migration_version(filename: str) -> int:
    match = _MIGRATION_NAME_RE.fullmatch(filename)
    if match is None:
        raise MigrationError(
            "Invalid migration filename "
            f"'{filename}'. Expected NNN_description.sql "
            "(for example 001_initial.sql)."
        )
    version = int(match.group(1))
    if version < 1:
        raise MigrationError(
            f"Migration version must be >= 1 (got {version} in {filename})"
        )
    return version


def _validate_migration_sequence(migrations: list[Migration]) -> list[Migration]:
    if not migrations:
        raise MigrationError("No migration files found")

    by_version: dict[int, Migration] = {}
    for migration in migrations:
        if migration.version in by_version:
            raise MigrationError(
                f"Duplicate migration version {migration.version:03d}: "
                f"{by_version[migration.version].name} and {migration.name}"
            )
        by_version[migration.version] = migration

    versions = sorted(by_version)
    expected = list(range(1, versions[-1] + 1))
    if versions != expected:
        raise MigrationError(
            "Migration versions must be contiguous starting at 001; "
            f"found {[f'{version:03d}' for version in versions]}"
        )
    return [by_version[version] for version in versions]


def _execute_sql_script(connection: sqlite3.Connection, sql: str) -> None:
    """Execute a multi-statement SQL script without auto-committing."""

    for statement in _split_sql_statements(sql):
        connection.execute(statement)


def _split_sql_statements(sql: str) -> list[str]:
    """Split a simple migration script into executable statements.

    Migration files are project-controlled DDL without string literals that
    contain semicolons, so a line-oriented split is sufficient.
    """

    statements: list[str] = []
    buffer: list[str] = []
    for raw_line in sql.splitlines():
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("--"):
            continue
        buffer.append(raw_line)
        if stripped.endswith(";"):
            statement = "\n".join(buffer).strip()
            if statement:
                statements.append(statement)
            buffer = []
    trailing = "\n".join(buffer).strip()
    if trailing:
        statements.append(trailing)
    return statements


def _repair_signals_v1_shape(connection: sqlite3.Connection) -> None:
    """Backfill missing signals columns on pre-versioned databases."""

    if not _table_exists(connection, "signals"):
        return

    existing = _table_columns(connection, "signals")
    for column_name, column_type in _SIGNALS_V1_COLUMNS.items():
        if column_name in existing:
            continue
        connection.execute(
            f"ALTER TABLE signals ADD COLUMN {column_name} {column_type}"
        )


def _table_exists(connection: sqlite3.Connection, table_name: str) -> bool:
    row = connection.execute(
        """
        SELECT 1
        FROM sqlite_master
        WHERE type = 'table' AND name = ?
        LIMIT 1
        """,
        (table_name,),
    ).fetchone()
    return row is not None


def _table_columns(connection: sqlite3.Connection, table_name: str) -> set[str]:
    rows = connection.execute(f"PRAGMA table_info({table_name})").fetchall()
    return {str(row[1]) for row in rows}
