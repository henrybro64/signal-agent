-- Agent memory tables for durable scoped knowledge (#65).
-- Schema version 2. Preserves existing signals/signal_runs/task_runs.

CREATE TABLE IF NOT EXISTS memory_documents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    namespace TEXT NOT NULL,
    source_path TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    title TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    UNIQUE(namespace, source_path)
);

CREATE TABLE IF NOT EXISTS memory_chunks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id INTEGER NOT NULL,
    chunk_id TEXT NOT NULL,
    content TEXT NOT NULL,
    token_estimate INTEGER NOT NULL DEFAULT 0,
    ordinal INTEGER NOT NULL,
    FOREIGN KEY(document_id) REFERENCES memory_documents(id) ON DELETE CASCADE,
    UNIQUE(document_id, chunk_id)
);

CREATE INDEX IF NOT EXISTS idx_memory_chunks_document_id
ON memory_chunks(document_id);

CREATE TABLE IF NOT EXISTS memory_operational (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    namespace TEXT NOT NULL,
    kind TEXT NOT NULL,
    key TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT,
    UNIQUE(namespace, kind, key)
);

CREATE INDEX IF NOT EXISTS idx_memory_operational_namespace_kind
ON memory_operational(namespace, kind);
