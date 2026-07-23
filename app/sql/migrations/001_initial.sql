-- Initial SQLite schema for signal-agent (schema version 1).
-- Applied by app.core.migrations via PRAGMA user_version.
-- sql/schema.sql is a docs pointer only; this file is the DDL source of truth
-- for version 1. Packaged as app.sql migrations resources for installed wheels.

CREATE TABLE IF NOT EXISTS signals (
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

CREATE UNIQUE INDEX IF NOT EXISTS idx_signals_url_unique
ON signals(url)
WHERE url IS NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS idx_signals_source_external_id_unique
ON signals(source, external_id)
WHERE external_id IS NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS idx_signals_content_hash_unique
ON signals(content_hash);

CREATE TABLE IF NOT EXISTS signal_runs (
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

CREATE TABLE IF NOT EXISTS task_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_name TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL,
    fetched_count INTEGER NOT NULL DEFAULT 0,
    new_count INTEGER NOT NULL DEFAULT 0,
    skipped_count INTEGER NOT NULL DEFAULT 0,
    notified_count INTEGER NOT NULL DEFAULT 0,
    error_message TEXT
);
