"""mnemo-pg2 DDL — native PG schema shapes (private + shared).

Table NAMES mirror the vendor wildcard enumeration
(tests/mnemo2_vendor_tables.txt, 36 names at mnemosyne d34b5df). Shapes are
PG-native: pgvector embeddings (no vec0/sqlite_vec), tsvector GENERATED
columns + GIN (no FTS5 content tables / sync triggers — ZERO CREATE TRIGGER
in this file, R3-friendly), timestamptz bitemporal columns.

kind='private' -> full mnemo2_<agent> table set.
kind='shared'  -> the ONE shared surface schema (cross-agent, R5): working
                  cores + embeddings + canonical read + validations.
"""
from __future__ import annotations

import re

VEC_DIM = 256

# Vendor CREATE TABLE wildcard parity (tests/mnemo2_vendor_tables.txt).
# Vendor vec0 virtual tables (vec_working, vec_episodes) are sqlite-only
# mechanisms (spec-phase0 B5) — their PG equivalent IS memory_embeddings,
# so they are intentionally absent here (documented, R3).
VENDOR_TABLES = (
    "annotations audit_log canonical_facts conflict_probe_pending conflicts"
    " consolidated_facts consolidation_log cost_entries data_chunks"
    " episodic_memory facts gists graph_edges harmonic_beliefs"
    " hygiene_audit_log media_assets media_moments memoria_facts"
    " memoria_instructions memoria_kg memoria_persona memoria_preferences"
    " memoria_timelines memories memory_audit_events memory_embeddings"
    " memory_events memory_resonance_log memory_validations query_cache"
    " scratchpad statements sync_memory_state sync_meta sync_outbox_ack"
    " triples working_memory"
).split()

_QUALIFIED = set()  # (none today; belt for future quoted literals)
_TABLE_RE = re.compile(r"CREATE TABLE(?: IF NOT EXISTS)? (\w+)")

# ---- cores + ops shared by both kinds --------------------------------------
_CORE = [
    """CREATE TABLE IF NOT EXISTS working_memory (
        id TEXT PRIMARY KEY,
        session_id TEXT NOT NULL DEFAULT '',
        channel_id TEXT,
        author_id TEXT,
        author_type TEXT,
        content TEXT NOT NULL,
        importance REAL NOT NULL DEFAULT 0.5,
        source TEXT NOT NULL DEFAULT 'user',
        scope TEXT NOT NULL DEFAULT 'session',
        veracity TEXT NOT NULL DEFAULT 'unknown',
        tier TEXT,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        last_reinforced_at TIMESTAMPTZ,
        valid_until DATE,
        superseded_by TEXT,
        expired_at TIMESTAMPTZ,
        extract_entities BOOLEAN NOT NULL DEFAULT false,
        extract BOOLEAN NOT NULL DEFAULT false,
        metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
        search tsvector GENERATED ALWAYS AS (to_tsvector('english', content)) STORED
    )""",
    "CREATE INDEX IF NOT EXISTS wm_session_idx ON working_memory(session_id, created_at)",
    "CREATE INDEX IF NOT EXISTS wm_global_idx ON working_memory(scope, created_at DESC)",
    "CREATE INDEX IF NOT EXISTS wm_search_idx ON working_memory USING GIN (search)",
    """CREATE TABLE IF NOT EXISTS episodic_memory (
        id TEXT PRIMARY KEY,
        session_id TEXT NOT NULL DEFAULT '',
        summary TEXT NOT NULL,
        detail TEXT,
        topic TEXT,
        tier TEXT,
        importance REAL NOT NULL DEFAULT 0.5,
        source_memory_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        valid_until DATE,
        superseded_by TEXT,
        search tsvector GENERATED ALWAYS AS (to_tsvector('english', coalesce(summary,'') || ' ' || coalesce(detail,''))) STORED
    )""",
    "CREATE INDEX IF NOT EXISTS em_search_idx ON episodic_memory USING GIN (search)",
    """CREATE TABLE IF NOT EXISTS memories (
        id TEXT PRIMARY KEY,
        content TEXT,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )""",
    f"""CREATE TABLE IF NOT EXISTS memory_embeddings (
        memory_id TEXT PRIMARY KEY,
        kind TEXT NOT NULL DEFAULT 'working',
        embedding VECTOR({VEC_DIM}) NOT NULL,
        norm REAL,
        model TEXT,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )""",
    """CREATE TABLE IF NOT EXISTS graph_edges (
        source_id TEXT NOT NULL,
        target_id TEXT NOT NULL,
        edge_type TEXT NOT NULL DEFAULT 'related_to',
        weight REAL NOT NULL DEFAULT 0.5,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        valid_until TIMESTAMPTZ,
        PRIMARY KEY (source_id, target_id, edge_type)
    )""",
    "CREATE INDEX IF NOT EXISTS ge_source_idx ON graph_edges(source_id)",
    "CREATE INDEX IF NOT EXISTS ge_target_idx ON graph_edges(target_id)",
    """CREATE TABLE IF NOT EXISTS triples (
        id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        subject TEXT NOT NULL,
        predicate TEXT NOT NULL,
        object TEXT NOT NULL,
        valid_from TIMESTAMPTZ NOT NULL DEFAULT now(),
        valid_until TIMESTAMPTZ,
        source TEXT NOT NULL DEFAULT '',
        confidence REAL NOT NULL DEFAULT 1.0,
        source_memory_id TEXT
    )""",
    "CREATE INDEX IF NOT EXISTS triples_sp_idx ON triples(subject, predicate) WHERE valid_until IS NULL",
    """CREATE TABLE IF NOT EXISTS scratchpad (
        id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        content TEXT NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )""",
    """CREATE TABLE IF NOT EXISTS canonical_facts (
        id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        category TEXT NOT NULL,
        name TEXT NOT NULL,
        body TEXT NOT NULL,
        source TEXT NOT NULL DEFAULT '',
        confidence REAL NOT NULL DEFAULT 1.0,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        superseded_by BIGINT,
        valid_until DATE
    )""",
    # PG has no "UNIQUE ... WHERE" inline syntax: partial unique index instead
    "CREATE UNIQUE INDEX IF NOT EXISTS cf_open_idx ON canonical_facts(category, name) WHERE valid_until IS NULL",
    """CREATE TABLE IF NOT EXISTS memory_validations (
        id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        memory_id TEXT NOT NULL,
        bank TEXT NOT NULL DEFAULT 'private',
        action TEXT NOT NULL,
        validator TEXT NOT NULL,
        author_original TEXT,
        note TEXT,
        history JSONB NOT NULL DEFAULT '[]'::jsonb,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )""",
    "CREATE INDEX IF NOT EXISTS mv_mem_idx ON memory_validations(memory_id)",
    """CREATE TABLE IF NOT EXISTS audit_log (
        id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        tool TEXT NOT NULL,
        args JSONB NOT NULL DEFAULT '{}'::jsonb,
        result_status TEXT,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )""",
    """CREATE TABLE IF NOT EXISTS recall_diagnostics (
        tier TEXT PRIMARY KEY,
        hits BIGINT NOT NULL DEFAULT 0,
        fallbacks BIGINT NOT NULL DEFAULT 0,
        calls BIGINT NOT NULL DEFAULT 0,
        updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )""",
    """CREATE TABLE IF NOT EXISTS meta (
        key TEXT PRIMARY KEY,
        value TEXT
    )""",
]

_PRIVATE_ONLY = [
    # persona layer (private schemas only — vendor parity)
    """CREATE TABLE IF NOT EXISTS memoria_persona (
        id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        body TEXT NOT NULL,
        tier TEXT NOT NULL DEFAULT 'long_term',
        topic TEXT,
        source_memory_id TEXT,
        reinforcement_count BIGINT NOT NULL DEFAULT 0,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        last_reinforced_at TIMESTAMPTZ,
        demoted_at TIMESTAMPTZ
    )""",
    """CREATE TABLE IF NOT EXISTS memoria_preferences (
        id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        body TEXT NOT NULL,
        reason TEXT,
        persona_id BIGINT,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )""",
    """CREATE TABLE IF NOT EXISTS memoria_instructions (
        id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        body TEXT NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )""",
    """CREATE TABLE IF NOT EXISTS memoria_facts (
        id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        subject TEXT, predicate TEXT, object TEXT,
        body TEXT,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )""",
    """CREATE TABLE IF NOT EXISTS memoria_kg (
        id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        node TEXT NOT NULL,
        edge TEXT,
        payload JSONB NOT NULL DEFAULT '{}'::jsonb,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )""",
    """CREATE TABLE IF NOT EXISTS memoria_timelines (
        id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        label TEXT NOT NULL,
        entries JSONB NOT NULL DEFAULT '[]'::jsonb,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )""",
    # facts / notes cores
    """CREATE TABLE IF NOT EXISTS facts (
        id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        subject TEXT, predicate TEXT, object TEXT,
        body TEXT,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )""",
    """CREATE TABLE IF NOT EXISTS gists (
        id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        memory_id TEXT,
        gist TEXT,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )""",
    """CREATE TABLE IF NOT EXISTS statements (
        id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        memory_id TEXT,
        text TEXT NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )""",
    """CREATE TABLE IF NOT EXISTS consolidated_facts (
        id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        body TEXT NOT NULL,
        source_memory_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )""",
    """CREATE TABLE IF NOT EXISTS annotations (
        id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        memory_id TEXT,
        kind TEXT,
        body TEXT,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )""",
    # ops / sync / housekeeping
    """CREATE TABLE IF NOT EXISTS consolidation_log (
        id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        session_id TEXT,
        n_working BIGINT,
        n_episodic BIGINT,
        dry_run BOOLEAN DEFAULT false,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )""",
    """CREATE TABLE IF NOT EXISTS conflicts (
        id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        category TEXT, name TEXT,
        old_memory_id TEXT, new_memory_id TEXT,
        resolution TEXT,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )""",
    """CREATE TABLE IF NOT EXISTS conflict_probe_pending (
        id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        category TEXT, name TEXT,
        body TEXT,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )""",
    """CREATE TABLE IF NOT EXISTS memory_events (
        id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        op TEXT NOT NULL,
        memory_id TEXT,
        payload JSONB NOT NULL DEFAULT '{}'::jsonb,
        device_id TEXT,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )""",
    """CREATE TABLE IF NOT EXISTS sync_memory_state (
        device_id TEXT NOT NULL,
        memory_id TEXT NOT NULL,
        last_seen_id BIGINT NOT NULL DEFAULT 0,
        PRIMARY KEY (device_id, memory_id)
    )""",
    """CREATE TABLE IF NOT EXISTS sync_meta (
        key TEXT PRIMARY KEY,
        value TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS sync_outbox_ack (
        id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        device_id TEXT,
        event_id BIGINT,
        acked_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )""",
    """CREATE TABLE IF NOT EXISTS query_cache (
        key TEXT PRIMARY KEY,
        payload JSONB,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )""",
    """CREATE TABLE IF NOT EXISTS memory_audit_events (
        id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        memory_id TEXT,
        event TEXT,
        payload JSONB NOT NULL DEFAULT '{}'::jsonb,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )""",
    """CREATE TABLE IF NOT EXISTS hygiene_audit_log (
        id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        action TEXT, detail TEXT,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )""",
    """CREATE TABLE IF NOT EXISTS cost_entries (
        id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        kind TEXT, tokens BIGINT, cost NUMERIC,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )""",
    """CREATE TABLE IF NOT EXISTS media_assets (
        id TEXT PRIMARY KEY,
        ref TEXT, modality TEXT, mime TEXT, title TEXT,
        status TEXT NOT NULL DEFAULT 'unavailable',
        created_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )""",
    """CREATE TABLE IF NOT EXISTS media_moments (
        id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        asset_id TEXT REFERENCES media_assets(id) ON DELETE CASCADE,
        memory_id TEXT, ts TEXT, caption TEXT,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )""",
    """CREATE TABLE IF NOT EXISTS memory_resonance_log (
        id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        memory_id TEXT, signal TEXT, weight REAL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )""",
    """CREATE TABLE IF NOT EXISTS memory_model_proposals (
        id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        category TEXT, name TEXT, body TEXT,
        status TEXT NOT NULL DEFAULT 'pending',
        created_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )""",
    """CREATE TABLE IF NOT EXISTS harmonic_beliefs (
        id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        body TEXT, weight REAL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )""",
    """CREATE TABLE IF NOT EXISTS data_chunks (
        id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        memory_id TEXT, seq INT, text TEXT,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )""",
]

# Tables intentionally NOT created in the shared surface schema: the
# persona layer and media ledger are private-schema-only by vendor design
# (shared_* tools need only the memory cores below).
_SHARED_EXCLUDE = {
    "memoria_persona", "memoria_preferences", "memoria_instructions",
    "memoria_facts", "memoria_kg", "memoria_timelines",
    "media_assets", "media_moments",
}


# Cross-agent visibility views (R5): private agents see the shared surface
# through fixed view names in THEIR OWN schema, so one read path serves both
# banks. Views (not tables): DROP SCHEMA shared CASCADE can never orphan a
# private schema (the view just errors until the next ensure — YAGNI-safe).
# Target name FULLY QUALIFIED to the bake schema: a bare CREATE VIEW
# resolves its target through search_path — under pg_catalog-pinned
# paths (the CREATE TABLE batch in pgconn._own_ddl) it would fail or
# land in the wrong schema. Qualified = deterministic everywhere.
# {SELF} is substituted by shared_view_statements()/bake_view_statements()
# ONLY (string replace, never psycopg %s interpolation — these run inside
# a DO $$ block where a stray '%' is a syntax error).
_SHARED_VIEWS = [
    "DO $$ BEGIN IF to_regclass('{SH}.working_memory') IS NOT NULL THEN CREATE OR REPLACE VIEW {SELF}.shared_memory AS SELECT * FROM {SH}.working_memory; END IF; END $$",
    "DO $$ BEGIN IF to_regclass('{SH}.memory_embeddings') IS NOT NULL THEN CREATE OR REPLACE VIEW {SELF}.shared_embeddings AS SELECT * FROM {SH}.memory_embeddings; END IF; END $$",
    "DO $$ BEGIN IF to_regclass('{SH}.canonical_facts') IS NOT NULL THEN CREATE OR REPLACE VIEW {SELF}.shared_canonical AS SELECT * FROM {SH}.canonical_facts; END IF; END $$",
]

_SANDBOX_SHARED_RE = __import__("re").compile(r"^[a-z0-9_]+$")
_REF_ON_RE = re.compile(r"\b(ON(?: TO)? )([a-z_][a-z0-9_]*)(\s*)(?![\w.])",
                        re.IGNORECASE)


def _qualify_refs(stmt: str, schema: str) -> str:
    """Attach `schema`. to every bare relation reference in a DDL
    statement (CREATE INDEX ... ON t, ALTER TABLE, etc.) — but NEVER to
    statements already carrying a qualified name or to the cross-agent
    VIEW statements (they reference shared.* / mnemo2_* on purpose)."""
    if "." in stmt.split("(")[0]:
        return stmt
    if stmt.lstrip().upper().startswith(("CREATE OR REPLACE VIEW",
                                         "CREATE VIEW")):
        return stmt
    return _REF_ON_RE.sub(lambda m: m.group(1) + f'"{schema}".' + m.group(2)
                         + m.group(3), stmt)


def shared_view_statements(shared_schema: str | None = None) -> list[str]:
    # {SELF} (view TARGET schema) = the shared schema itself: these are
    # the self-pointing views baked INSIDE the shared schema (shared-kind
    # Conn). Bake-into-own-schema is bake_view_statements(), which
    # substitutes both placeholders — leaving {SELF} unreplaced here made
    # the shared bootstrap's DO block a SyntaxError that rolled back the
    # ENTIRE bootstrap txn (shared tables never created -> deterministic
    # UndefinedTable, and the r8/shared tests' original failure too).
    return bake_view_statements(shared_schema) if shared_schema \
        else bake_view_statements(__import__("os").environ.get(
            "MNEMO2_SHARED_SCHEMA", "shared"))


def bake_view_statements(bake_schema: str, shared: str | None = None) -> list[str]:
    """Views baked INTO `bake_schema` pointing at `shared` (env default).
    Both names validated; targets fully qualified (search_path-proof)."""
    for name, sh in ((bake_schema, shared),):
        if not _SANDBOX_SHARED_RE.match(name):
            raise ValueError(f"invalid schema name {name!r}")
    sh = shared or __import__("os").environ.get("MNEMO2_SHARED_SCHEMA", "shared")
    if not _SANDBOX_SHARED_RE.match(sh):
        raise ValueError(f"invalid shared schema name {sh!r}")
    return [s.replace("{SH}", sh).replace("{SELF}", bake_schema)
            for s in _SHARED_VIEWS]


def statements(kind: str = "private",
                self_schema: str | None = None) -> list[str]:
    sql = ["CREATE EXTENSION IF NOT EXISTS vector"]
    groups = _CORE + (_PRIVATE_ONLY if kind == "private" else
                     [s for s in _PRIVATE_ONLY
                      if not any(x in s.split()[:6] for x in _SHARED_EXCLUDE)
                      and not any(f"EXISTS {t} " in s for t in _SHARED_EXCLUDE)])
    # R8 race class: CREATE TABLE IF NOT EXISTS resolves the target
    # relation through search_path. Two concurrent bootstraps (a private
    # and a shared-kind Conn) holding DIFFERENT search_paths can resolve
    # the SAME unqualified name to different schemas; their IF-NOT-EXISTS
    # catcache skips then miss and both INSERT into pg_type ->
    # pg_type_typname_nsp_index UniqueViolation (surface write escaping
    # into the private schema). Qualify to the Conn's own schema, always.
    # NOT pgconn.agent_schema/shared_schema: ddl.py is deliberately
    # import-standalone (test_r3) and importing pgconn here pulls psycopg.
    # The Conn passes its resolved schema in; env fallback mirrors pgconn.
    if self_schema is None:
        _os = __import__("os")
        if kind == "shared":
            self_schema = _os.environ.get("MNEMO2_SHARED_SCHEMA", "shared")
        else:
            self_schema = _os.environ.get("MNEMO2_SCHEMA") or (
                "mnemo2_" + re.sub(r"[^a-z0-9_]", "_",
                    (_os.environ.get("HERMES_HOME", "")
                     .rstrip("/").rsplit("/", 1)[-1] or "default").lower()))
    if not _SANDBOX_SHARED_RE.match(self_schema):
        raise ValueError(f"invalid schema name {self_schema!r}")
    seen = set()
    for stmt in groups:
        stmt = stmt.replace("{VEC_DIM}", str(VEC_DIM))
        m = re.match(r"(CREATE TABLE(?: IF NOT EXISTS)? )(\w+)", stmt)
        if m and m.group(2) not in _QUALIFIED:
            stmt = f'{m.group(1)}"{self_schema}".{m.group(2)}' + \
                stmt[m.end():]
        else:
            # R8 race class (2): the qualification above covers CREATE
            # TABLE only. CREATE INDEX / ALTER TABLE / any other object
            # statement still resolves its target through search_path —
            # two concurrent bootstraps in different (fresh) schemas
            # "find" each other's working_memory, their IF-NOT-EXISTS
            # catcache skip misses after a snapshot race, and both
            # INSERT into pg_type -> pg_type_typname_nsp_index
            # UniqueViolation. Qualify every ` ON <table>` /
            # ` ON TO <table>`-style relation reference to OUR schema.
            stmt = _qualify_refs(stmt, self_schema)
        seen.add(stmt)
        sql.append(stmt)
    if kind == "private":
        # bake the cross-agent views INSIDE the bootstrap txn (the Conn
        # holds the advisory lock here) — never from a second, unlocked
        # connection afterwards (R8: CREATE OR REPLACE VIEW outside the
        # lock races the shared schema's own bootstrap on pg_type).
        # The bake reads MNEMO2_SHARED_SCHEMA exactly like the env
        # fallback above; a Conn with a DIFFERENT shared target re-bakes
        # under the lock itself (pgconn.Conn.__enter__).
        # RACE CLOSE: qualify the baked views to a schema, never bare —
        # a CREATE VIEW with no explicit target lands in the CURRENT
        # schema (the first search_path entry). The shared-surface
        # bootstrap runs with search_path = the shared schema, so an
        # overlapping private bootstrap baked shared_* INTO the shared
        # schema's namespace; the later real CREATE then collided on
        # pg_type_typname_nsp_index (the r8 surface-write race). The
        # private views belong in the private schema = self_schema.
        sql += bake_view_statements(self_schema)
    return sql


def created_tables(kind: str = "private") -> set[str]:
    out: set[str] = set()
    for stmt in statements(kind):
        m = _TABLE_RE.search(stmt)
        if m:
            out.add(m.group(1))
    return out
