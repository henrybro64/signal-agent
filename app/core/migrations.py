"""Lightweight SQLite schema migrations using PRAGMA user_version."""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path

_MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "sql" / "migrations"
_MIGRATION_NAME_RE = re.compile(r"^(\d{3})_([a-z0-9_]+)\.sql$")


class MigrationError(RuntimeError):
    """Raised when schema migrations cannot be listed or applied safely."""


def migrations_dir() -> Path:
    """Return the default on-disk migrations directory."""

    return _MIGRATIONS_DIR


def list_migration_files(
    migrations_path: Path | None = None,
) -> list[tuple[int, Path]]:
    """Return sorted ``(version, path)`` pairs from the migrations directory."""

    directory = migrations_path if migrations_path is not None else _MIGRATIONS_DIR
    if not directory.is_dir():
        raise MigrationError(f"Migrations directory not found: {directory}")

    found: dict[int, Path] = {}
    for path in sorted(directory.glob("*.sql")):
        match = _MIGRATION_NAME_RE.fullmatch(path.name)
        if match is None:
            raise MigrationError(
                "Invalid migration filename "
                f"'{path.name}'. Expected NNN_description.sql "
                "(for example 001_initial.sql)."
            )
        version = int(match.group(1))
        if version < 1:
            raise MigrationError(
                f"Migration version must be >= 1 (got {version} in {path.name})"
            )
        if version in found:
            raise MigrationError(
                f"Duplicate migration version {version:03d}: "
                f"{found[version].name} and {path.name}"
            )
        found[version] = path

    versions = sorted(found)
    if not versions:
        raise MigrationError(f"No migration files found in {directory}")

    expected = list(range(1, versions[-1] + 1))
    if versions != expected:
        raise MigrationError(
            "Migration versions must be contiguous starting at 001; "
            f"found {[f'{version:03d}' for version in versions]}"
        )

    return [(version, found[version]) for version in versions]


def get_schema_version(connection: sqlite3.Connection) -> int:
    """Return the database schema version from ``PRAGMA user_version``."""

    row = connection.execute("PRAGMA user_version").fetchone()
    if row is None:
        raise MigrationError("SQLite did not return a user_version value")
    return int(row[0])


def latest_migration_version(migrations_path: Path | None = None) -> int:
    """Return the highest packaged migration version."""

    return list_migration_files(migrations_path)[-1][0]


def apply_pending_migrations(
    connection: sqlite3.Connection,
    *,
    migrations_path: Path | None = None,
) -> int:
    """Apply pending SQL migrations in order and return the resulting version.

    Migrations should use idempotent DDL (``IF NOT EXISTS``) where practical so
    legacy unversioned databases and retries remain safe. On failure, the
    version is not advanced and a clear ``MigrationError`` is raised.
    """

    migrations = list_migration_files(migrations_path)
    latest = migrations[-1][0]
    current = get_schema_version(connection)

    if current > latest:
        raise MigrationError(
            f"Database schema version {current} is newer than the supported "
            f"version {latest}. Upgrade signal-agent before opening this DB."
        )

    for version, path in migrations:
        if version <= current:
            continue
        if version != current + 1:
            raise MigrationError(
                f"Cannot apply migration {path.name}: database is at version "
                f"{current}, expected to apply version {current + 1} next."
            )

        sql = path.read_text(encoding="utf-8")
        try:
            # executescript auto-commits; bump version only after SQL succeeds.
            connection.executescript(sql)
            connection.execute(f"PRAGMA user_version = {version}")
            connection.commit()
        except MigrationError:
            raise
        except Exception as exc:
            raise MigrationError(
                f"Failed applying migration {path.name} while upgrading from "
                f"schema version {current} to {version}: {exc}"
            ) from exc
        current = version

    return current
