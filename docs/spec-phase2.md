# lcm2-mnemo2-redesign — Phase 2 spec (lcm2-pg plugin, native PG)

Slug: lcm2-mnemo2-redesign · Phase: 2 · Worker: mike · Date: 2026-11-01 (SAST)
Plan binding: /home/mybuddy/fleet/plans/lcm2-mnemo2-redesign.plan.md
Source of truth: docs/spec-phase0.md PART A (A1–A5) + QA rows R3/R4/R7/R8/R11.

## 1. Scope

Plugin `lcm2-pg` = clean-room, PG-native re-implementation of the hermes-lcm
vendor surface (vendor: stephenschoettler/hermes-lcm @ a13e709) for compression
of the Hermes agent `lcm2agent` ONLY (scope: schemas `lcm2_*` on `lcm2db`,
port 54331). Zero sqlite code, zero sqlite fallback paths (R3). The other
plugin (mnemo-pg2) and mnemo2db shared-schema semantics are Phase 3 — untouched
here (R10).

## 2. Tool surface (15 tools + extras, parity with A1/A2)

All 15 vendor tools from `plugin.yaml provides_tools`, same names, same
param/result contracts at the tool boundary:
lcm_grep, lcm_recall, lcm_query_state, lcm_compute, lcm_compile_evidence,
lcm_evidence_pack, lcm_retrieve, lcm_recent, lcm_load_session, lcm_describe,
lcm_expand, lcm_expand_query, lcm_status, lcm_inspect, lcm_doctor.

Extras mirrored from vendor (A1 "Extras" row): `register_context_engine`
(the engine itself — ingest + compression), `subagent_start`/`subagent_stop`
hooks, `/lcm` slash command (env-gated), bundled-skill registration via
`register_skill` when the host provides it.

Honesty rule (binding for R4): every tool returns a well-formed result;
where a deep vendor sub-mechanism is simplified, the tool says so explicitly
in its output (`"implementation": "lcm2-pg/native", "simplified": [...]`) —
never a silent stub, never fake completeness.

## 3. Storage mapping (A-tables → PG)

Authoritative table set = the A4 **wildcard carry-over rule** (QA F2):
enumerate `CREATE TABLE` names FROM the pinned vendor source
(`grep -rhoE "CREATE TABLE( IF NOT EXISTS)? [a-zA-Z_0-9]+" <src> --include='*.py'
--exclude-dir=tests --exclude-dir=__pycache__ | awk '{print $NF}' | sort -u`)
— NOT A4's itemized prose. At vendor a13e709 this yields **37 tables**
(re-verified this turn; listed in tests/test_tables_parity.py). CREATE VIRTUAL
TABLE names (messages_fts, nodes_fts, lcm_trajectory_states_fts) are FTS5-only
artifacts and are replaced by tsvector GENERATED columns + GIN indexes, so
they carry over as *indexes*, not tables.

Rules:
- One private schema per agent (`lcm2_<agent>`), schema set via `search_path`;
  DDL is schema-agnostic (bare names) so it lands in the agent's own schema.
- `store_id` (sqlite rowid) → BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY.
- timestamps → timestamptz (converted at write edge from epoch-seconds).
- JSON text columns → JSONB where vendor stored JSON text.
- embeddings → pgvector `vector(dim)` on lcm_embedding_vectors + lcm_chunk_vectors
  (exact scan; HNSW deferred, YAGNI). No sqlite_vec / vec0 anywhere (absent by
  construction).
- FTS5 + external-content triggers → `tsvector GENERATED ALWAYS` columns +
  GIN indexes (no triggers needed; PG maintains generated columns).
- `metadata` table kept (vendor name; key/value bookkeeping).

## 4. Config surface (YAGNI subset, documented)

Resolution order per plan §4: profile `.env` → global `.env` → defaults.
- `LCM2_PG_DSN` (profile .env) — primary; composed from global
  `LCM2_PG_HOST/PORT/USER` when absent.
- `LCM2_AGENT_SCHEMA` override; default derived: `lcm2_<basename(HERMES_HOME)>`.
- `LCM_CONTEXT_THRESHOLD` honored exactly like vendor (yaml key
  `lcm.context_threshold` read via host config; env wins).
- `LCM2_SUMMARY_MODEL` (default alias `aux`) for compression summaries —
  routes through the host's LLM router; `LCM2_DISABLE_MODEL=1` forces
  deterministic extractive summaries (used by tests).
Other vendor LCM_* knobs are NOT ported in Phase 2 (YAGNI; Phase A/B may add).

## 5. Module layout

plugins/lcm2-pg/
  plugin.yaml            name lcm2-pg, provides_tools (15), provides_hooks
  __init__.py            register(ctx): context engine + tools + hooks + /lcm
  pg.py                  psycopg pool, schema resolution, DDL bootstrap (idempotent)
  db.py                  thin query helpers (all SQL here, parameterized)
  engine.py              context engine: ingest, DAG, compression, assembly
  tools.py               the 15 tool handlers (return JSON strings, vendor-shaped)
  command.py             /lcm slash handler
  hooks.py               pre_llm_call injection + subagent_start/stop recorders
Tests live in repo tests/lcm2/ (pytest), run against the REAL instance
(socket ~/lcm2-mnemo2-pg/pg/run, port 54331, db lcm2db) — the same connection
path the plugin uses in production. Tests use throwaway schemas
lcm2_test_* and drop them after.

## 6. TDD ordering (R11)

Commit 1: docs/spec-phase2.md + tests/ (red). Commit 2: implementation (green).
Commit 3+: fixes + live-proof evidence. Tests exercise the TOOL SURFACE
(tools.py handlers + engine) against lcm2db on 54331, never sqlite.

## 7. Inherited fixes (Phase-1 QA carry-over)

- README documents the `~/lcm-pg/pg/bin` PostgreSQL binary dependency (F5).
- onboard_profile.sh stale usage-comment line fixed.
- phase1.result.md line-4 SHA drift corrected (1c38396 → feeedb5, note kept).
- Fix: profile .env DSN lines were mangled by Phase-1 set_param appends
  (concatenated duplicates); regenerated cleanly for both test agents.
