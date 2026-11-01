"""Idempotent DDL for the lcm2_<agent> schema (native PG).

Carries over EVERY table enumerated from the pinned vendor source via the
A4 wildcard rule (37 CREATE TABLE names at hermes-lcm a13e709), mapped to
native PG shapes:
  - INTEGER PRIMARY KEY AUTOINCREMENT/rowid -> BIGINT GENERATED ALWAYS AS IDENTITY
  - epoch-seconds REAL timestamps           -> timestamptz
  - JSON-in-TEXT columns                    -> JSONB
  - FTS5 virtual tables + sync triggers     -> tsvector GENERATED columns + GIN
  - embedded vector stores        -> pgvector vector(dim) columns
"""
from __future__ import annotations

SCHEMA_VERSION = "2"

TABLES: list[tuple[str, str]] = [
    # -- core ----------------------------------------------------------
    ("messages", """
        store_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        session_id TEXT NOT NULL,
        source TEXT NOT NULL DEFAULT '',
        conversation_id TEXT NOT NULL DEFAULT '',
        role TEXT NOT NULL,
        content TEXT,
        tool_call_id TEXT,
        tool_calls JSONB,
        tool_name TEXT,
        timestamp TIMESTAMPTZ NOT NULL,
        token_estimate INTEGER NOT NULL DEFAULT 0,
        pinned BOOLEAN NOT NULL DEFAULT FALSE,
        ingested_at TIMESTAMPTZ,
        observed_at TIMESTAMPTZ,
        observed_at_source TEXT,
        search tsvector GENERATED ALWAYS AS
            (to_tsvector('english', coalesce(content, ''))) STORED
    """),
    ("metadata", "key TEXT PRIMARY KEY, value TEXT"),
    ("summary_nodes", """
        node_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        session_id TEXT NOT NULL,
        depth INTEGER NOT NULL DEFAULT 0,
        summary TEXT NOT NULL,
        token_count INTEGER NOT NULL DEFAULT 0,
        source_token_count INTEGER NOT NULL DEFAULT 0,
        source_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
        source_type TEXT NOT NULL DEFAULT 'messages',
        created_at TIMESTAMPTZ NOT NULL,
        earliest_at TIMESTAMPTZ,
        latest_at TIMESTAMPTZ,
        expand_hint TEXT NOT NULL DEFAULT '',
        search tsvector GENERATED ALWAYS AS
            (to_tsvector('english', coalesce(summary, ''))) STORED
    """),
    ("lcm_lifecycle_state", "singleton BOOLEAN PRIMARY KEY DEFAULT TRUE, state JSONB, updated_at TIMESTAMPTZ DEFAULT now(), CHECK (singleton)"),
    ("lcm_migration_state", "singleton BOOLEAN PRIMARY KEY DEFAULT TRUE, schema_version INTEGER NOT NULL DEFAULT 0, updated_at TIMESTAMPTZ DEFAULT now(), CHECK (singleton)"),
    # -- embeddings (pgvector) ------------------------------------------
    ("lcm_embedding_meta", "singleton BOOLEAN PRIMARY KEY DEFAULT TRUE, meta JSONB, updated_at TIMESTAMPTZ DEFAULT now(), CHECK (singleton)"),
    ("lcm_embedding_vectors", "store_id BIGINT PRIMARY KEY, embedding vector(1024), updated_at TIMESTAMPTZ DEFAULT now()"),
    ("lcm_embedding_binary", "store_id BIGINT PRIMARY KEY, bits BYTEA, updated_at TIMESTAMPTZ DEFAULT now()"),
    ("lcm_embedding_profile", "profile_key TEXT PRIMARY KEY, profile JSONB, updated_at TIMESTAMPTZ DEFAULT now()"),
    ("lcm_embedding_backfill_inflight", "job_key TEXT PRIMARY KEY, payload JSONB, started_at TIMESTAMPTZ DEFAULT now()"),
    ("lcm_chunk_meta", "chunk_id TEXT PRIMARY KEY, meta JSONB, updated_at TIMESTAMPTZ DEFAULT now()"),
    ("lcm_chunk_binary", "chunk_id TEXT PRIMARY KEY, bits BYTEA, updated_at TIMESTAMPTZ DEFAULT now()"),
    ("lcm_chunk_vectors", "chunk_id TEXT PRIMARY KEY, embedding vector(1024), updated_at TIMESTAMPTZ DEFAULT now()"),
    # -- assertions -----------------------------------------------------
    ("lcm_assertions", "id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY, subject_key TEXT, predicate_key TEXT, object_value TEXT, kind TEXT, scope_key TEXT, valid_at TIMESTAMPTZ, superseded_at TIMESTAMPTZ, created_at TIMESTAMPTZ DEFAULT now(), extra JSONB"),
    ("lcm_assertion_sources", "assertion_id BIGINT, store_id BIGINT, span JSONB, quote TEXT"),
    ("lcm_assertion_relations", "from_id BIGINT, to_id BIGINT, relation TEXT, weight REAL"),
    # -- rollups --------------------------------------------------------
    ("lcm_rollups", "rollup_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY, session_id TEXT, period_start TIMESTAMPTZ, period_kind TEXT, content TEXT, search tsvector GENERATED ALWAYS AS (to_tsvector('english', coalesce(content, ''))) STORED, created_at TIMESTAMPTZ DEFAULT now()"),
    ("lcm_rollup_sources", "rollup_id BIGINT, source_id BIGINT, source_type TEXT"),
    ("lcm_rollup_state", "singleton BOOLEAN PRIMARY KEY DEFAULT TRUE, state JSONB, updated_at TIMESTAMPTZ DEFAULT now(), CHECK (singleton)"),
    ("lcm_rollup_invalidations", "id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY, session_id TEXT, invalidated_at TIMESTAMPTZ DEFAULT now(), reason TEXT"),
    # -- query views ----------------------------------------------------
    ("lcm_query_views", "view_key TEXT PRIMARY KEY, definition JSONB, created_at TIMESTAMPTZ DEFAULT now()"),
    ("lcm_query_view_sources", "view_key TEXT, store_id BIGINT, weight REAL"),
    ("lcm_query_view_versions", "view_key TEXT, version BIGINT, state JSONB, created_at TIMESTAMPTZ DEFAULT now()"),
    ("lcm_query_corpus_events", "event_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY, session_id TEXT, event JSONB, created_at TIMESTAMPTZ DEFAULT now()"),
    ("lcm_query_corpus_state", "singleton BOOLEAN PRIMARY KEY DEFAULT TRUE, state JSONB, updated_at TIMESTAMPTZ DEFAULT now(), CHECK (singleton)"),
    # -- trajectory -----------------------------------------------------
    ("lcm_trajectory_assets", "asset_key TEXT PRIMARY KEY, payload JSONB, created_at TIMESTAMPTZ DEFAULT now()"),
    ("lcm_trajectory_corpora", "corpus_key TEXT PRIMARY KEY, payload JSONB, created_at TIMESTAMPTZ DEFAULT now()"),
    ("lcm_trajectory_embeddings", "embedding_key TEXT PRIMARY KEY, embedding vector(1024), payload JSONB, created_at TIMESTAMPTZ DEFAULT now()"),
    ("lcm_trajectory_embedding_profiles", "profile_key TEXT PRIMARY KEY, payload JSONB, created_at TIMESTAMPTZ DEFAULT now()"),
    ("lcm_trajectory_sources", "source_key TEXT PRIMARY KEY, payload JSONB, created_at TIMESTAMPTZ DEFAULT now()"),
    ("lcm_trajectory_states", "state_key TEXT PRIMARY KEY, session_id TEXT, state JSONB, search tsvector GENERATED ALWAYS AS (to_tsvector('english', coalesce(state #>> '{}', ''))) STORED, updated_at TIMESTAMPTZ DEFAULT now()"),
    ("lcm_trajectory_state_embeddings", "state_key TEXT PRIMARY KEY, embedding vector(1024), updated_at TIMESTAMPTZ DEFAULT now()"),
    ("lcm_trajectory_state_embedding_profiles", "profile_key TEXT PRIMARY KEY, payload JSONB, created_at TIMESTAMPTZ DEFAULT now()"),
    ("lcm_trajectory_transitions", "transition_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY, from_state TEXT, to_state TEXT, payload JSONB, created_at TIMESTAMPTZ DEFAULT now()"),
    ("lcm_trajectory_ingest_receipts", "receipt_key TEXT PRIMARY KEY, payload JSONB, created_at TIMESTAMPTZ DEFAULT now()"),
    # -- import scripts -------------------------------------------------
    ("lcm_imported_messages", "store_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY, session_id TEXT, role TEXT, content TEXT, timestamp TIMESTAMPTZ, source_ref TEXT"),
    ("lcm_imported_summaries", "node_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY, session_id TEXT, depth INTEGER, summary TEXT, source_ids JSONB, created_at TIMESTAMPTZ"),
]

INDEXES = """
CREATE INDEX IF NOT EXISTS idx_messages_session_ts ON messages(session_id, timestamp);
CREATE INDEX IF NOT EXISTS idx_messages_search ON messages USING GIN (search);
CREATE INDEX IF NOT EXISTS idx_nodes_session_depth ON summary_nodes(session_id, depth, node_id);
CREATE INDEX IF NOT EXISTS idx_nodes_search ON summary_nodes USING GIN (search);
CREATE INDEX IF NOT EXISTS idx_rollups_session_period ON lcm_rollups(session_id, period_start);
CREATE INDEX IF NOT EXISTS idx_rollups_search ON lcm_rollups USING GIN (search);
CREATE INDEX IF NOT EXISTS idx_assertions_subject ON lcm_assertions(subject_key, predicate_key);
CREATE INDEX IF NOT EXISTS idx_imported_messages_session ON lcm_imported_messages(session_id);
"""


def apply_schema(conn) -> None:
    """Create the extension + every table/index idempotently."""
    conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
    conn.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    for name, cols in TABLES:
        conn.execute(f"CREATE TABLE IF NOT EXISTS {name} ({cols})")
    for stmt in (s.strip() for s in INDEXES.split(";") if s.strip()):
        conn.execute(stmt)
    conn.execute(
        "INSERT INTO lcm_migration_state(singleton, schema_version) "
        "VALUES (TRUE, 0) ON CONFLICT (singleton) DO NOTHING")
    conn.execute(
        "UPDATE lcm_migration_state SET schema_version = %s, updated_at = now()",
        (int(SCHEMA_VERSION),))
    conn.execute(
        "INSERT INTO metadata(key, value) VALUES ('schema_version', %s) "
        "ON CONFLICT (key) DO UPDATE SET value = excluded.value",
        (SCHEMA_VERSION,))
