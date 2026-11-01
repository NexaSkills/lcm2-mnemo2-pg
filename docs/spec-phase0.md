# lcm2-mnemo2-redesign — Phase 0 Recon Spec (reverse-engineering inventory + PG mapping)

Slug: lcm2-mnemo2-redesign · Phase: 0 · Worker: mike · Date: 2026-11-01 (SAST)
AMENDMENT 2026-11-01: applies QA findings F1–F7 (tessa verdict PARTIAL,
fleet/reports/lcm2-mnemo2-redesign.phase0.qa.md). All corrected numbers below were
re-measured against the pinned §0 commits before writing; markers "RETRACTED"/
"CORRECTION (QAn)" identify each changed claim.
Plan (binding): /home/mybuddy/fleet/plans/lcm2-mnemo2-redesign.plan.md
Method: Superpowers (obra/superpowers v7.0.1 @ 2ce8aa15 loaded on mike's profile via
`skills.external_dirs`; plan-first, spec-before-code). No code written this phase.
All vendor/fork/plugin dirs accessed READ-ONLY.

## 0. Sources studied (verified commits)

| Source | Location studied | Commit (verified `git log -1`) |
|---|---|---|
| Vendor hermes-lcm (github.com/stephenschoettler/hermes-lcm) | /home/mybuddy/lcm-upstream (pre-existing clean clone, pulled) | a13e709c2547 2026-10-17 "flatten lcm_compile_evidence (#624)" |
| Vendor mnemosyne (github.com/mnemosyne-oss/mnemosyne) | /home/mybuddy/lcm2-recon/mnemosyne (fresh clone, depth 50) | d34b5df79f40 2026-10-31 "Merge PR #1297 embed-query-backend-race" |
| Our fork lcm-pg (github.com/NexaSkills/lcm-pg) | /home/mybuddy/lcm2-recon/lcm-pg (fresh clone; ~/.hermes/forks/lcm-pg untouched) | 3313b15281ea 2026-10-30 "factory._pg_dsn: widen resolution" |
| Our plugin mnemo-pg | /home/mybuddy/.hermes/plugins/mnemo-pg (existing install, read-only) | version "0.7.4-mnemo-pg" (plugin/plugin.yaml — path corrected per QA E19) |
| Superpowers (github.com/obra/superpowers) | /home/mybuddy/lcm2-recon/superpowers (fresh clone, depth 1) | 2ce8aa15d05b 2026-10-21 "Release v7.0.1" |

Code volume: hermes-lcm 181 .py files; mnemosyne 505 .py files (core ~70 modules in
mnemosyne/core/). Memory tool + mnemosyne context confirm the vendor surfaces observed
in live sessions match these sources.

---

# PART A — hermes-lcm (vendor sqlite) → lcm2-pg (db `lcm2db`, schema `lcm2_<agent>`)

## A1. Tools (15, from plugin.yaml `provides_tools`, register verified via tools.py/schemas.py)

| # | Tool | Function (from schemas.py/tools.py semantics) | lcm2-pg native-PG design |
|---|---|---|---|
| 1 | lcm_grep | FTS5/semantic/hybrid search over session messages + summary nodes; scopes current/all/session; time bounds; externalized-payload opt-in | `to_tsvector('english', …)` GIN index on messages.content + summary nodes; ts_rank + recency + importance fusion replaces RRF-over-FTS5; ILIKE substring fallback for CJK/emoji fragments implemented in SQL (pg_trgm optional); same param surface (mode/sort/content_scope/limit caps) |
| 2 | lcm_recall | Cross-conversation semantic recall (summaries+verbatim), scope_bias, detail=snippets/answer_ready, seen_refs delta cursor | Global scan over `lcm2_<agent>` messages/summary_nodes using embedding cosine (pgvector) + tsquery; per-session diversity + ref hydration done with window functions instead of python scan loops |
| 3 | lcm_query_state | V4 assertion sidecar: typed, source-cited current/historical state (subject/predicate/scope/as-of) | Direct SELECTs on lcm_assertions* tables (see A4); `as_of` = temporal predicate `valid_at <= $1 AND (superseded_at IS NULL OR superseded_at > $1)` — native bitemporal columns, no sqlite date-string hacks |
| 4 | lcm_compute | Deterministic date/count/unit/difference/order/latest-state ops over exact cited operands; fails closed | Same contract; operands validated against exact spans via PK+OFFSET checks in SQL; pure-python arithmetic retained (no LLM) |
| 5 | lcm_compile_evidence | Bounded evidence brief from exact refs; proposal/auto modes; answer-sufficient/finite-coverage states | Same contract (flattened schema per upstream #624); quote/span validation = `substring(content from start for len) = quote` SQL check |
| 6 | lcm_evidence_pack | Lower-level hydration/pack builder (facets, dedup, canonical trace) | Same; ref hydration via PK lookups; budgets enforced with LIMIT in SQL |
| 7 | lcm_retrieve | Bounded retrieval-episode controller (start/search/finish/status/abandon), ≤3 dispatched tool calls, no model client | Same state-machine; episode state rows in a new `lcm2_trajectory`-side table or existing trajectory stores |
| 8 | lcm_recent | UTC-period rollups (today/yesterday/Nd/week/month/date:/last Nh) with leaf fallback | lcm_rollups/lcm_rollup_state queried by `date_trunc`/tstzrange predicates; rollup_builder maintenance keeps semantics |
| 9 | lcm_load_session | Ordered raw transcript pagination for one session_id (cursor after_store_id) | `WHERE session_id=$1 AND store_id>$2 ORDER BY store_id LIMIT $3` — store_id becomes BIGINT/IDENTITY (see A4 sqlite mechanisms) |
| 10 | lcm_describe | Summary-node subtree metadata OR externalized-payload metadata (no content) | Metadata from summary_nodes + externalized sidecar manifest rows; token counts from stored column |
| 11 | lcm_expand | Recover detail: node_id (current session) / externalized_ref / store_id modes; token budget; pagination | Recursive CTE to descend DAG (WITH RECURSIVE replaces python BFS); cross-session store_id expand works natively (single DB, schema-qualified) |
| 12 | lcm_expand_query | Auxiliary-LLM Q&A over expanded context; recursive DAG descent under budget | Same; aux model routing untouched (host-side), context assembled via recursive CTE |
| 13 | lcm_status | Engine health: counts, DAG depth distribution, usage, filters, rotate backup state | SQL aggregates (count(*), depth histogram via GROUP BY) — no sqlite pragma stats |
| 14 | lcm_inspect | Read-only metadata inventory (lifecycle, fresh tail, externalized refs, ignore patterns) | Same; reads lifecycle_state row + information_schema-independent bookkeeping tables |
| 15 | lcm_doctor | Integrity + repair: PRAGMA integrity_check equivalent, orphan DAG detection, config validation | PG equivalents: `amcheck` (optional, verify availability at Phase 1 — else skip layer), FK/orphans via anti-join queries, `lcm_migration_state` parity table |

Extras (not in provides_tools but live in vendor): `/lcm` slash command (register_command
when LCM_ENABLE_SLASH_COMMAND), register_skill (recalls the hermes-lcm skill),
register_context_engine + conditional register_tool (toolset "context_engine",
message-forwarding capability check).

## A2. Hooks (plugin.yaml `provides_hooks`)

| Hook | Vendor behavior | lcm2-pg design |
|---|---|---|
| pre_llm_call | `_on_pre_llm_call`: injects recall-policy guidance + proactive-recall context (LCM_PROACTIVE_RECALL_*), preanswer-evidence, prompt-boundary, fresh-tail accounting; engine-bound-to-session check via `on_session_start` | Same hook contract; all reads/writes via psycopg to lcm2_<schema>; context-engine registration path (message-aware vs message-blind hosts) preserved exactly |
| subagent_start | aux_session.record_subagent_start (subagent session lineage) | Row insert into aux/lifecycle tables (schema-qualified) |
| subagent_stop | aux_session.record_subagent_stop | Same |
| (command) | slash `/lcm` handler → command.py | Same register_command path |
| (skill) | register_skill for recall-policy text | Copy skill bundle into repo skills/, symlinked like plugin |

## A3. Config surface (vendor hermes-lcm)

- config.yaml: ONLY `lcm.context_threshold` is honored
  (`_SUPPORTED_LCM_CONFIG_YAML_KEYS = {"context_threshold"}`; other keys reported
  ignored); fallback chain → `compression.threshold` (unless compression disabled) →
  default. Env `LCM_CONTEXT_THRESHOLD` wins over yaml.
- Env vars: **175 distinct `LCM_*` names over all `.py` files** (`grep -rhoE
  "LCM_[A-Z0-9_]+" . --include='*.py' | sort -u` = 175; 174 excluding tests/; the
  top-level-only command in the old §Reproducibility yields 170 and the word-bounded
  form 120 — earlier draft's "218" reproduced none of these scopes and is
  RETRACTED per QA F1). Families (name prefixes; full verbatim list reproducible
  with the scoped command above): context/tokens (LCM_CONTEXT_THRESHOLD, LCM_MAX_ASSEMBLY_TOKENS,
  LCM_RESERVE_TOKENS_FLOOR, LCM_L2_BUDGET_RATIO, LCM_L3_TRUNCATE_TOKENS,
  LCM_FRESH_TAIL_COUNT/-MAX_TOKENS, LCM_LEAF_CHUNK_TOKENS, LCM_DYNAMIC_LEAF_CHUNK_*,
  LCM_NEW_SESSION_RETAIN_DEPTH, LCM_INCREMENTAL_MAX_DEPTH, LCM_CONDENSATION_FANIN,
  LCM_CACHE_FRIENDLY_*); DB/path (LCM_DATABASE_PATH [default HERMES_HOME/lcm.db],
  LCM_HERMES_BASE_DIR, LCM_EXTRACTION_OUTPUT_PATH, LCM_LARGE_OUTPUT_EXTERNALIZATION_*);
  models (LCM_SUMMARY_MODEL, LCM_SUMMARY_FALLBACK_MODELS, LCM_EXPANSION_MODEL,
  LCM_EXTRACTION_MODEL, LCM_ASSERTION_EXTRACTION_MODEL, LCM_SELECTIVE_COMPILER_MODEL,
  LCM_OLLAMA_BASE_URL); spend-guard (LCM_SUMMARY_SPEND_*, LCM_EMBEDDING_QUERY_SPEND_*,
  circuit breaker); embeddings (LCM_EMBEDDINGS_ENABLED, LCM_EMBEDDING_MODEL/PROVIDER/
  STORE_DIM/STORAGE_DTYPE/BINARY_PRESCREEN/BACKFILL_*, KNN_PRESCREEN_MULTIPLIER);
  FTS/retrieval (LCM_GREP_*, LCM_RECALL_* incl. RRF_K/ARM_WEIGHTS/scan budgets,
  LCM_SEMANTIC_MAX_WORKERS, LCM_FULL_TEXT_MAX_WORKERS, LCM_TITLE_BOOST,
  LCM_ADAPTIVE_RETRIEVAL_ENABLED, LCM_RETRIEVE_*, LCM_QUERY_STATE_*, LCM_COMPUTE_*,
  LCM_EVIDENCE_PACK, LCM_COMPILE_EVIDENCE, LCM_EXPAND*, LCM_LOAD_SESSION_*,
  LCM_RECENT_*, LCM_INSPECT_*, LCM_STATUS, LCM_GREP/RECALL/LCM_* RESPONSE_CHAR_CAP);
  rollups (LCM_TEMPORAL_ROLLUPS_ENABLED, LCM_ROLLUP_*); assertions
  (LCM_ASSERTIONS_ENABLED, LCM_ASSERTION_EXTRACTION_*); maintenance
  (LCM_DEFERRED_MAINTENANCE_*, LCM_FTS_INTEGRITY_*, LCM_EMPTY_LIFECYCLE_GC_*,
  LCM_THRESHOLD_FULL_SWEEP_ENABLED, LCM_LARGE_OUTPUT_TRANSCRIPT_GC_ENABLED);
  filters/patterns (LCM_IGNORE_SESSION_PATTERNS, LCM_STATELESS_SESSION_PATTERNS,
  LCM_IGNORE_MESSAGE_PATTERNS, LCM_SENSITIVE_PATTERNS*); recall (LCM_PROACTIVE_RECALL_*,
  LCM_PREANSWER_EVIDENCE_*); misc (LCM_ENABLE_SLASH_COMMAND, LCM_CUSTOM_INSTRUCTIONS,
  LCM_ANTIBOILERPLATE, LCM_DOCTOR_CLEAN_APPLY_ENABLED).
- **lcm2-pg param resolution (plan §4 binding):** every knob above resolves
  global-first from /home/mybuddy/.hermes/.env, profile override from
  /home/mybuddy/.hermes/profiles/<agent>/.env (same names or LCM2_ prefix decided in
  Phase-2 spec; convention: keep vendor names for drop-in familiarity, add LCM2_* DB
  params: LCM2_PG_DSN, LCM2_PG_SCHEMA=<lcm2_agent>). config.yaml writes ONLY via
  `hermes [-p p] config get/set/unset`. DB binding replaces LCM_DATABASE_PATH entirely
  (no per-profile file exists).

## A4. DB tables (vendor; **49 distinct names from `CREATE TABLE` across all `.py`** —
37 defined in non-test source + 12 test-only names (10 plain CREATE TABLE fixtures:
conversations, sessions, summaries, summary_messages, summary_parents, message_parts,
legacy, lcm_querycache, lcm_future_widgets, lcm_assertion_future_state, plus the
messages_fts/nodes_fts names, which are CREATE VIRTUAL TABLE in tests/test_lcm_core.py
and CREATE TABLE'd nowhere in non-test source; per QA F2, 2026-11-01 amendment).
Corrections to the earlier draft: the "40 in non-test source" count is RETRACTED, and
the 5 query-corpus/query-view tables + 10 lcm_trajectory_* tables were already itemized
and are real (trajectory_store.py, query_view_store.py) — the genuinely missing source
tables are lcm_imported_messages + lcm_imported_summaries (scripts/
import_lossless_claw.py).

Core: messages, metadata, summary_nodes, lcm_lifecycle_state, lcm_migration_state.
FTS (virtual): messages_fts, nodes_fts (fts5; external-content tables per
ExternalContentFtsSpec — auto-sync via sqlite 'delete'/'insert' trigger shim).
Embeddings (lazy, `embeddings_v1` marker, SCHEMA_VERSION=5 core ladder):
lcm_embedding_meta, lcm_embedding_vectors (plain BLOB table, db_bootstrap.py:1354 —
NOT vec0, see A5 correction), lcm_embedding_binary,
lcm_embedding_profile, lcm_embedding_backfill_inflight; chunk twins: lcm_chunk_meta,
lcm_chunk_binary, lcm_chunk_vectors.
Assertions: lcm_assertions, lcm_assertion_sources, lcm_assertion_relations.
Rollups: lcm_rollups, lcm_rollup_sources, lcm_rollup_state, lcm_rollup_invalidations
(+ I/U/D triggers on summary_nodes feeding invalidations — AFTER INSERT/UPDATE OF
session_id). Query views: lcm_query_views, lcm_query_view_sources,
lcm_query_view_versions, lcm_query_corpus_events, lcm_query_corpus_state (all five
CREATE TABLE'd in query_view_store.py). Trajectory: lcm_trajectory_assets,
lcm_trajectory_corpora, lcm_trajectory_embeddings,
lcm_trajectory_embedding_profiles, lcm_trajectory_sources, lcm_trajectory_states,
lcm_trajectory_state_embeddings, lcm_trajectory_state_embedding_profiles,
lcm_trajectory_transitions, lcm_trajectory_ingest_receipts (all ten CREATE TABLE'd in
trajectory_store.py). Import scripts: lcm_imported_messages, lcm_imported_summaries
(scripts/import_lossless_claw.py).
**Wildcard carry-over rule (binding for R4/R3 test generation, per QA F2):** the
enumeration above is documentary; authoritative table set for parity tests =
`grep -rhoE "CREATE TABLE( IF NOT EXISTS)? [a-zA-Z_]+" <src> --include='*.py'
--exclude-dir=tests --exclude-dir=__pycache__ | awk '{print $NF}' | sort -u` run
against the pinned vendor commit, NOT this itemized list.

**lcm2db/lcm2_<agent> mapping:** 1:1 table carry-over (same names; schema-qualified),
with native-PG type shapes: store_id rowid → BIGINT GENERATED ALWAYS AS IDENTITY
PRIMARY KEY; content/tool payloads → TEXT (unicode-safe) or JSONB where vendor stored
JSON text; embeddings → pgvector `vector(dim)` column on lcm_embedding_vectors (+ HNSW
or exact-scan — dim fixed by LCM_EMBEDDING_STORE_DIM); binary/residual vectors →
bytea + precomputed binary bit ops (int8[]/bit) instead of vec0 binary prescreen;
timestamps → timestamptz (vendor epoch-seconds REAL columns converted at write edge);
FTS → tsvector GENERATED ALWAYS columns + GIN (replaces messages_fts/nodes_fts virtual
tables AND their sync triggers); migration parity → lcm_migration_state with
schema_version semantics kept. NO shared schema in lcm2db (plan §2).

## A5. sqlite mechanisms in vendor source (the "why we can't wrap it" list)

Verified by grep: `import sqlite3` in **24 non-test files** (52 repo-wide incl.
tests; the earlier "17+ core modules" understated, per QA F7); 22 distinct PRAGMA
names. The 6 omitted by the original list: `foreign_key_check`, `is`,
`schema_version`, `settings`, `user_version`, `verification` — note `is` is a
false-positive from the sentence "…the pragma is a plain read…" (db_bootstrap.py:150)
and `settings`/`verification` occur only in comments; executed-form additions are
schema_version, user_version (`foreign_key_check` occurs only as the query form
`PRAGMA foreign_key_check`, 3 occurrences, per QA N2 — listed under query forms
below, not set/executed forms).
- PRAGMA set (executed forms): busy_timeout(30s), journal_mode(WAL), synchronous,
  journal_size_limit, mmap_size, wal_autocheckpoint, wal_checkpoint, foreign_keys,
  foreign_key_check, query_only, integrity_check, quick_check, table_info,
  index_list/index_info/index_xinfo, database_list, schema_version, user_version
  (store.py, db_bootstrap.py, sqlite_util.py, maintenance.py, etc.); the remaining
  distinct-name matches in the 22-name set (`is`, `settings`, `verification`) are
  comment/prose occurrences, not executed PRAGMAs (verified by grep).
- FTS5 virtual tables with external-content + sync triggers; bm25() ranking;
  snippet()/pickle; MATCH query language (search_query.py escapes/normalizes)
- CORRECTION (QA F4, RETRACTED claim): hermes-lcm uses NO vec0/sqlite_vec/
  knn_search — 0 hits repo-wide; lcm_embedding_vectors is a plain BLOB table
  (db_bootstrap.py:1354) with a separate binary-prescreen table. The
  binary-prescreen-in-plain-tables mechanism is what carries over (pgvector +
  bit/hamming arm in lcm2-pg design).
- rowid AUTOINCREMENT identity + `store_id` cursor pagination; `_rowid_` joins
- ATTACH-free single-file DB per profile (HERMES_HOME/lcm.db), 0700 dir / 0600 file
  hygiene (store.py _prepare_private_sqlite_storage), :memory: special-case
- Schema ladder SCHEMA_VERSION=5 + named migration-state markers
  (SchemaVersionTooNewError refuse-open), disk-space floor (50MB), busy-retry loops
  (SQLITE_BUSY), backup/rotate (maintenance.py; sqlite3 .backup analog),
  threading-local connections + lru_cache on bootstrap.
**PG replacements (design, Phase-2 spec materializes):** psycopg3 pool replaces
thread-local connections (LCM_PG_POOL_* params proven in our fork); lock contention via
MVCC + lock_timeout instead of busy_timeout retry; FTS5→tsvector/GIN; BLOB-table
embeddings→pgvector (no vec0 existed in the vendor, see correction above);
rowid→IDENTITY; PRAGMA introspection→information_schema/pg_catalog (our fork already
built native_introspect.py — proven pattern); integrity_check→amcheck-or-explicit
checks in lcm_doctor; WAL checkpoint maintenance → autovacuum/no-op; :memory: tests →
temp schemas or test DBs. **No fallback branches allowed** (plan: any sqlite fallback
= FAIL, R3).

## A6. Known pain points from our lcm-pg fork (what lcm2-pg must not repeat)

From lcm-pg clone (backends/: factory.py, postgres.py, sqlite3_compat.py, shim_ddl.py,
fts_pg.py, fts_conn.py, triggerdef_pl.py, native_introspect.py, ddl_lock.py,
trace_parity.py):
1. **sqlite3 shim translation layer**: whole sqlite3_compat backend rewriting SQL
   strings at runtime (regex rewrite of FTS MATCH, PRAGMA→introspection, DELETE-FROM-
   USING shims); every new vendor merge risks unhandled SQL shapes — the structural
   parity gap Jan caught is exactly this class of failure.
2. sqlite fallback branch still reachable when LCM_PG_DSN unresolved (fails toward
   sqlite, not fail-fast).
3. Trigger-name collisions across one-schema-per-profile (same trigger name in many
   schemas; postgres.py comment about fleet trigger lesson); shim DDL race-prone
   helpers (lcm_shim_race_prone, ddl_lock.py advisory-ns 776600x).
4. Env surface added just to make the shim work (LCM_PG_DSN/SCHEMA/POOL_*,
   LCM_PG_VENV_SITE, LCM_PG_RESHIM_AFTER_CREATE, LCM_PG_DEBUG_PRAGMA,
   LCMPG_-style probes); isolated-mode `-I` launcher strips PYTHONPATH → venv-site
   appending hacks in loader.
5. FTS parity kept as "119 FTS / 100 GIN" grep-visible patch surface
   (fts_pg.py rewrites vs native tsvector).
lcm2-pg answer: native psycopg SQL authored against PG from the start; no translator
module at all; DSN resolution single deterministic path (global .env → profile .env →
fail-fast with actionable error); advisory-locked idempotent DDL per schema (keep this
proven fork practice); fail-fast R3 (force-fail a PG op → error surfaces, never sqlite).

---

# PART B — vendor mnemosyne (sqlite) → mnemo-pg2 (db `mnemo2db`, schemas `mnemo2_<agent>` + `shared`)

## B1. Tools

Two vendor manifests exist (both inventoried; plugin.yaml files verified by cat):
- integrations/hermes/src/mnemosyne_hermes/plugin.yaml (hermes-mnemosyne 0.7.7): 20 tools.
- hermes_memory_provider/plugin.yaml (mnemosyne 4.0.0 — superset, matches the LIVE tool
  surface of this session): 34 tools. mnemo-pg2 implements the 4.0.0 superset.

| # | Tool | Vendor behavior | mnemo2-pg design (schema-private + shared) |
|---|---|---|---|
| 1 | mnemosyne_remember | Write working memory (importance, scope session/global, valid_until, extract_entities/extract triples, veracity) — BEAM write path + embeddings | INSERT into working_memory in caller's schema; embeddings → vec_working; entity/triple fan-out same transaction |
| 2 | mnemosyne_recall | Hybrid recall: vec_weight/fts_weight/importance_weight + temporal decay (query_time, halflife), explain trace, per-tier fallbacks | Ranked SQL: cosine (pgvector) + ts_rank_cd + importance + recency in one CTE; weights from env/config exactly like vendor defaults (0.5/0.3/0.2); FTS5→tsvector |
| 3 | mnemosyne_stats | working/episodic counts, BEAM tiers | COUNT/GROUP BY aggregates |
| 4 | mnemosyne_triple_add | Temporal KG triple (supersede closes prior sub-pred triples) | triples table with valid_from/valid_until timestamptz + partial unique index; supersede = UPDATE…WHERE valid_until IS NULL returning closed row |
| 5 | mnemosyne_triple_query | Pattern query + as_of point-in-time | WHERE valid_from<=as_of AND (valid_until IS NULL OR >as_of) |
| 6 | mnemosyne_triple_end | Expire triple without replacement (bank sub) | UPDATE close; bank arg routes to shared |
| 7 | mnemosyne_sleep | Consolidation cycle (working→episodic compression, all_sessions/dry_run/force) | Set-based SQL + LLM summarize calls identical; advisory lock per schema prevents concurrent sleep (fork-proven locking) |
| 8-10 | mnemosyne_scratchpad_write/read/clear | Ephemeral notes table | scratchpad table in schema (shared bank NOT used here — vendor parity) |
| 11 | mnemosyne_invalidate | Expire/supersede memory with replacement chain (bank private/surface) | UPDATE valid_until/superseded_by with chain columns; bank='surface' routes `shared` schema |
| 12 | mnemosyne_export | Dump all memories to JSON file | COPY … TO STDOUT JSON (native) or SELECT+json_build; file write host-side |
| 13 | mnemosyne_import | Import from JSON file or Hindsight/Mem0 providers (idempotent) | Upserts on natural keys; provider fetch code unchanged (network, not storage) |
| 14 | mnemosyne_update | Change content/importance by id (re-embed) | UPDATE + embedding invalidation trigger-parity (pg trigger or explicit, Phase-2 spec decides; explicit preferred — YAGNI) |
| 15 | mnemosyne_forget | Delete by id | DELETE (private/surface via bank) |
| 16 | mnemosyne_diagnose | PII-safe install diagnostics (+ vec_working coverage repair) | checks rewritten against pg_catalog (vector ext present, schema shape, index presence) |
| 17 | mnemosyne_graph_query | BFS multi-hop over graph_edges (edge_type/min_weight) | RECURSIVE CTE with cycle-guard (array of visited) — native, faster, no python queue |
| 18 | mnemosyne_graph_link | Declare edge (source,target,relationship,weight) | INSERT into graph_edges (upsert on pair+type) |
| 19-21 | mnemosyne_sync_push/pull/status | Incremental CRDT-ish sync to remote w/ conflict resolution (timestamp+importance), cursor/device-id | event-log tables + outbox ack; keep contract; PG gives native replication alternative but vendor contract kept (plan: no extra engines) |
| 22 | mnemosyne_batch | Atomic remember/update/forget/invalidate ops (≤50, rollback) | Single PG transaction — native strength (sqlite had deferred BEGIN IMMEDIATE) |
| 23 | mnemosyne_get | Pure read by PK | SELECT by PK |
| 24 | mnemosyne_validate | Attest/update/invalidate/delete with ring-buffer history, any-agent attestation (private+surface) | UPDATE + validation_history jsonb column (3-entry ring kept) |
| 25-27 | mnemosyne_remember/recall/forget_canonical | Canonical slot per (category,name): single source, no-op on same body, supersede keeps history | UNIQUE (category,name) with partial index WHERE valid_until IS NULL; retire=stamp not delete |
| 28-31 | mnemosyne_persona_promote/demote/list/reinforce | L3 persona store promotion tiers, reinforcement counters, list order permanent-first | persona table + tier ordering CASE; reinforcement UPDATE bumps |
| 32 | mnemosyne_shared_remember | Cross-agent surface write (kind meta/preference/correction/identity) | INSERT into **mnemo2db.shared** schema tables — the plan §2 shared schema replaces vendor "surface bank" concept exactly |
| 33 | mnemosyne_shared_recall | Search only shared surface | Same query path against shared schema |
| 34 | mnemosyne_shared_forget | Delete shared entry by id | DELETE in shared |
| 35 | mnemosyne_shared_stats | Shared DB path + counts | aggregate in shared |
| 36 | mnemosyne_model_card | Deterministic render of canonical slots (no LLM) | SQL over canonical slots + render |
| 37 | mnemosyne_model_refresh | Inspect sleep-time LLM-inferred canonical proposals | proposals table read |
| 38 | mnemosyne_task_progress | Cross-session task state via canonical category 'task:progress' | canonical slots with category pattern |
| 39 | mnemosyne_recall_diagnostics | Per-tier recall hit/fallback counters | counters table in schema |
| 40 | mnemosyne_resolve_conflicts | Global-scope contradiction resolution: stale copy superseded (needs CROSS_SESSION_CONFLICT_RESOLUTION=1) | pair-key conflicts table + supersede update |

(Note: 36-40 exist as live tools in this session's tool surface. Definitions now
cited by file:line (QA F6): integrations/hermes/src/mnemosyne_hermes/tools.py:148
mnemosyne_shared_stats, :183 mnemosyne_resolve_conflicts, :483 mnemosyne_model_card,
:507 mnemosyne_model_refresh, :706 mnemosyne_recall_diagnostics, :726
mnemosyne_task_progress. shared_stats also appears in the 34-tool
hermes_memory_provider/plugin.yaml manifest; 36-40 are defined in the hermes
integration's tools.py + __init__.py registration, not in the two plugin.yaml
manifests' provides_tools lists; vendor mcp_tools.py has no hits for 36-40 names.)

## B2. Hooks (both manifests: `pre_llm_call`, `on_session_start`, `post_tool_call`)

- on_session_start: profile/session binding, prefetch warmup.
- pre_llm_call: renders "MNEMOSYNE CONTEXT" block (prefetch recall + persona.md
  injection; provider-active flag silences legacy overlap).
- post_tool_call: auto-capture hooks/audit (audit.py), recall provenance.
mnemo-pg2 keeps the three hooks 1:1, with writes landing in the caller's
mnemo2_<agent> schema and shared writes only via the shared_* tools.

## B3. Config surface (vendor)

- **218 distinct `MNEMOSYNE_*` names over all `.py` files** (`grep -rhoE
  "MNEMOSYNE_[A-Z0-9_]+" . --include='*.py' | sort -u` = 218; 209 names in
  source-only scope with tests/ excluded — the earlier draft cited 218 without
  stating scope; the number stands now that scope is explicit, per QA F1).
  Families: paths (MNEMOSYNE_HOME/DATA_DIR/DB_PATH/BLOB_DIR/BACKUP_DIR/MODEL_DIR/
  MODEL_CACHE_DIR/FASTEMBED_CACHE_DIR), banks (MNEMOSYNE_BANK(_*), DEFAULT_SCOPE,
  CHANNEL_ID, AUTHOR_ID/TYPE), embeddings (EMBEDDING_MODEL/API_URL/API_KEY/DIM/
  MAX_CHARS/MAX_BYTES/PREFIXES, EMBEDDINGS_VIA_API/NO_EMBEDDINGS/FORCE_LOCAL),
  ranking weights (FTS_WEIGHT, IMPORTANCE_WEIGHT, VEC via recall args, BINARY_BONUS,
  FACT_BONUS, GRAPH_BONUS, RECENCY_HALFLIFE, TITLE-ish boosts), LLM (LLM_PROVIDER/
  MODEL/BASE_URL/API_KEY/N_CTX/N_THREADS/TIMEOUT/FALLBACK_*/EXTRA_BODY,
  HOST_LLM_*, CONFLICT_LLM_*, EXTRACTION_MODEL/PROMPT, HEAL_*), consolidation
  (AUTO_SLEEP*, CONSOLIDATION_TIER, CROSS_SESSION*, CROSS_TIER_DEDUP,
  DREAM_ACTIVE), persona (PERSONA_ENABLED/FILE/INTERVAL/TOKEN_CAP/DAILY_SYNC_HOUR),
  media (MEDIA_ALLOWED_PATHS, MODALITY_* incl. VISION/AUDIO/VIDEO models), sync
  (JOURNAL_MODE, sync server knobs), MCP server (MCP_TOKEN(S)/ALLOWED_HOSTS/BANK),
  recall behaviour (PREFETCH_*, POLYPHONIC_RECALL, RECALL_*, QUERY_INTENT,
  ENHANCED_RECALL, LENIENT_FACT_MATCH, LEXICAL_GATE_MIN), fleet (FLEET_CENSUS/ROOT),
  BEAM (BEAM_MODE/OPTIMIZATIONS).
- **mnemo-pg2 resolution (plan §4):** global from /home/mybuddy/.hermes/.env,
  profile override from profile .env. New DB params: MNEMO2_PG_DSN,
  MNEMO2_SCHEMA (default mnemo2_<agent> via deterministic naming — adopt the
  fork's schema_for_profile pattern, no hardcoded profile lists),
  MNEMO2_SHARED_SCHEMA=shared. Vendor MNEMOSYNE_* names kept unchanged where the
  behavior is unchanged.

## B4. DB tables (vendor; CREATE TABLEs in core/, grep-verified; probe/test-only names excluded)

Memory cores: working_memory, episodic_memory, memory_embeddings (embedding blob +
metadata), vec_working/vec_episodes (vec0 mirrors), fts stores (fts5 external-content:
fts_*/memories_fts-style with content_rowid + sync triggers).
Facts/graph: triples (temporal bitemporal), graph_edges, annotations, memoria_facts.
CORRECTION (QA F3): the earlier "entities (entities.py fan-out)" entry is RETRACTED —
no `entities` table exists (0 CREATE TABLE hits). core/entities.py is pure
regex/entity-extraction code with zero SQL; its docstring (entities.py:8) documents
the fan-out target as the TripleStore `triples` table (subject=memory_id,
predicate="mentions", object=entity_name) — already listed above. (Dropped alongside:
"memories" is absent from the core inventory; note it IS CREATE TABLE'd in
core/memory.py:373 (legacy backward-compat dual-write table alongside
working_memory; also scripts/migrate_from_legacy.py:55) and tests/ fixtures —
excluded by design, per QA N1.)
Persona/canonical layer: memoria_persona, memoria_preferences, memoria_instructions,
memoria_kg, memoria_timelines, canonical_facts, consolidated_facts, gists, statements,
data_chunks (verbatim ledger), verbatim ledger companions.
Operations: scratchpad, audit_log, hygiene_audit_log, memory_audit_events,
memory_validations, memory_events (sync event log), sync_memory_state, sync_meta,
sync_outbox_ack, cost_entries, query_cache, conflicts, conflict_probe_pending,
consolidation_log, media_assets, media_moments, memory_resonance_log, meta,
changed_after_gate.
Shared surface ("surface bank" in vendor): separate sqlite DB (mnemosyne_shared /
surface bank paths) holding the same memory/embedding/FTS shapes — mnemo-pg2 replaces
this file-with-**one `shared` schema in mnemo2db** (plan §2): shared tables =
shared.working_memory-equivalent + shared surface variants of
memory_embeddings/vec/fts, canonical slots for cross-agent kind=meta/preference/
correction/identity.

## B5. sqlite-specific mechanisms in mnemosyne vendor (fail-point inventory)

Line-count scope now stated (QA F5 — grep -rn, all .py / source-only excl. tests):
`sqlite_master` 128 / 47, `vec0` 195 / 63, `sqlite_vec` 176 / 44,
`sqlite_vec_binary` 2 / 2, `fts5` 14 / 6 (core/beam.py line 1567 `rowid INTEGER
PRIMARY KEY AUTOINCREMENT`, 1754 content_rowid='rowid', 1769/1774 external-content
sync triggers with 'delete'/'insert' commands); CREATE TRIGGER sites 40 / 11 (the
earlier "7 raw trigger bodies" was one narrower reading). Also present:
shmr.py (shared-memory read mode), _connection_gc/connection_release (connection
lifetime hacks), sqlite_config.py (busy_timeout/journal tuning), migrations
e6_triplestore_split / e7_311_tables / e8_conflict_pair_key (version-gated ALTER
programs).
PG replacements for mnemo2db: pgvector (`vector` + HNSW/IVFFlat, plus bit/hamming for
the binary-prescreen arm — our mnemo-pg already proved a "pgvector blob-search arm in
core/beam.py"), tsvector+GIN (ts_rank_cd) for FTS5, trigger-parity via PL/pgSQL
(our triggerdef_pl.py pattern) or explicit dual-writes, jsonb for payload/event
columns, timestamptz bitemporal columns on triples, per-schema advisory locks
(hashlib keying proven in provision_schema.py), IDENTITY for rowid, and NO
sqlite_master introspection (pg_catalog instead).

## B6. Known pain points of our mnemo-pg (what mnemo-pg2 must not repeat)

From /home/mybuddy/.hermes/plugins/mnemo-pg (loader __init__.py 199 lines read +
scripts/ survey):
1. **Vendor-shim architecture**: binds every `sqlite3.connect` in vendored upstream
   through a translation backend (mnemo_backends/vendor_bind) — vendor core stays
   sqlite-first; every upstream merge re-opens shim gaps (exact class of the
   "structural parity" failures Jan caught on lcm-pg).
2. Path fragility: MNEMO_VENDOR_DIR/layout A-vs-B detection, site-packages shadowing
   (pip-installed mnemosyne could silently bypass vendored pgvector patch), psycopg
   invisible under Hermes `-I` isolated launcher (venv-site append hack).
3. Legacy co-existence machinery: _neutralize_legacy.py + quarantine scripts (old
   sqlite plugin must never co-load — memory note confirms boot-order drop-ins needed).
4. Migration/convergence script sprawl: 50+ scripts (converge_*, retire_*, heal_*,
   shape_pass, ensure_*) because schema drifted between sqlite-shaped DDL and PG
   reality; retired-sqlite quarantine backups churn.
5. Shared surface via separate DB path (surface bank) instead of first-class schema —
   cross-agent write serialization had no PG-level guarantees.
mnemo-pg2 answers: native core (no vendor/ dir at all), one deterministic
DSN/naming path, `shared` as a real schema with row-level locking, single
idempotent DDL generator (keep provision_schema's advisory-lock design), legacy
never co-loads (fail-fast check at load).

---

# PART C — Cross-cutting design mapping (binding plan §2 restated as inventory)

1. ONE new Postgres instance under /home/mybuddy (systemd user service, distinct
   port/socket; existing lcm-pg cluster :54329 untouched — R1).
2. lcm2db: schemas lcm2_<agent> ONLY (no shared). mnemo2db: mnemo2_<agent> + shared.
   Deterministic schema naming derived from profile dir name (both forks proved this).
3. Test agents lcm2agent/mnemo2agent created via fleet profile-onboarding practice;
   all other profiles untouched (R10).
4. Plugin delivery: repo checkout once under /home/mybuddy; global + per-profile
   plugin dirs are symlinks (R6); params global .env → profile .env (R5); config.yaml
   only via `hermes config set` (R5); zero sqlite in code paths (R3) — enforced by
   grep + force-fail runtime test.
5. Parity target = Part A1/A2 + B1/B2 tool/hook tables (R4 probes live tool surface
   inside lcm2agent/mnemo2agent sessions).
6. End-to-end proof = compression cycle rows in lcm2_lcm2agent; private+shared memory
   rows in mnemo2db (R7); concurrency via two-schema parallel smoke (R8 — PG MVCC
   gives this natively, unlike WALite sqlite writers).

# Reproducibility (evidence commands)

All inventories above are reproducible with grep/git against the exact commits in §0.
Every count below states its scope (Phase-0 amendment per QA F1; earlier draft
commands here did not yield the cited numbers):
- tools: `cat <repo>/plugin.yaml` (both repos) — counts 15 (lcm) / 20+34 (mnemosyne manifests)
- tables: `grep -rhoE "CREATE TABLE( IF NOT EXISTS)? [a-z_]+" <src> --include='*.py'
  --exclude-dir=__pycache__ | awk '{print $NF}' | sort -u` → 49 names (hermes-lcm,
  all .py) / 37 (excluding tests/); A4's wildcard rule is authoritative for R4 tests.
- env: `grep -rhoE "LCM_[A-Z0-9_]+" lcm-upstream --include='*.py' | sort -u` → 175
  names (174 excluding tests/); `grep -rhoE "MNEMOSYNE_[A-Z0-9_]+" mnemosyne
  --include='*.py' | sort -u` → 218 names (209 excluding tests/).
- pragmas/mechanisms (all .py unless noted): `import sqlite3` files: 24 non-test /
  52 repo-wide (lcm); PRAGMA names 22 (lcm, see A5 note re: comment-only matches);
  mnemosyne line-hits: sqlite_master 128 (47 src), vec0 195 (63 src), sqlite_vec
  176 (44 src), sqlite_vec_binary 2, fts5 14 (6 src); CREATE TRIGGER 40 (11 src).
  hermes-lcm vec0/sqlite_vec/knn_search: 0 hits (A5 correction).
- tool 36-40 definitions: `grep -n '"mnemosyne_..."' <repo>/integrations/hermes/src/
  mnemosyne_hermes/tools.py` (lines in B1 note).
- hooks: `provides_hooks` in plugin.yaml + `register_hook(` call sites

## Exit / QA notes for tessa (R-matrix hooks)

- R4 seed: tool/hook tables A1/A2/B1/B2 are the checklist.
- R3 seed: sqlite-mechanism lists A5/B5 name exactly what must be absent in shipped code.
- R11: this file + in-repo docs/spec-phase0.md + TDD-ordered commits in
  NexaSkills/lcm2-mnemo2-pg are the Superpowers evidence chain.

---
STATUS: Phase 0 COMPLETE — inventory written + mirrored to docs/spec-phase0.md of NexaSkills/lcm2-mnemo2-pg. NEXT: Phase 1 scaffolding (new PG instance under /home/mybuddy, lcm2db+mnemo2db+schemas for lcm2agent/mnemo2agent, repo scaffold + symlink layout + onboarding script), then tessa spot-check of this recon against upstream. Echo-back: LCM2-MNEMO2-P0.
