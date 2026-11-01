# lcm2-mnemo2-redesign — Phase 3 spec (mnemo-pg2 plugin, native PG memory)

Slug: lcm2-mnemo2-redesign · Phase: 3 · Worker: mike · Date: 2026-11-02 (SAST)
Plan binding: /home/mybuddy/fleet/plans/lcm2-mnemo2-redesign.plan.md
Source of truth: docs/spec-phase0.md PART B (B1–B5) + QA rows R3/R5/R7/R8/R11.
Phase-2 lessons applied VERBATIM (from lcm2-pg, qa-r6 PASS @1ac93e0):
  (a) P2FLIP3 discipline — never publish/take host-owned bare sys.modules
      names outside our own import window; the `_selfimport.anchor()` pattern
      (file-anchored loads + origin-gated meta-path finders) is mirrored here
      (this plugin imports the SAME anchor module from lcm2-pg's directory by
      file, under namespaced keys — see §7).
  (b) origin-gated finders (serve OUR bare sibling imports only inside our
      exec window / from our own frames).
  (c) commit+push first, wip authorized; final 5 min reserved.

## 1. Scope

Plugin `mnemo-pg2` = clean-room, PG-native re-implementation of the vendor
mnemosyne memory surface (vendor: mnemosyne-oss/mnemosyne @ d34b5df79f40) for
the Hermes agents `mnemo2agent` (and `lcm2agent` as the cross-agent reader in
live proof C). Storage = `mnemo2db` ONLY (dedicated instance
`lcm2-mnemo2-pg.service`, socket ~/lcm2-mnemo2-pg/pg/run, port 54331):
one private schema `mnemo2_<agent>` per agent PLUS exactly ONE shared schema
`shared` (cross-agent surface; the plan §2 replacement for the vendor
"surface bank" separate-DB concept). Zero sqlite code, zero sqlite fallback
paths (R3): no `import sqlite3`, no embedded-DB branch, force-failed PG ops
surface as errors.

## 2. Tool surface — the WILDCARD RULE is authoritative (R4/R11)

Authoritative enumeration rule (spec-phase0 binding pattern): tool names =
union of the vendor `provides_tools` manifests (all four plugin.yaml files,
tests excluded) MERGED with the mnemonic tool names documented in
spec-phase0 §B1 (incl. the 5 integration-registered extras 36–40 cited by
file:line there). The itemised table is documentary; the checked-in lists
tests/mnemo2_vendor_tools_wildcard.txt + tests/mnemo2_vendor_tables.txt are
what the parity tests read.

- tests/mnemo2_vendor_tools_manifests.txt = 41 names from
  `grep -A80 provides_tools` over integrations/{hermes/src/mnemosyne_hermes,
  hermes-catalog}/plugin.yaml + hermes_memory_provider/plugin.yaml (union,
  deduped; the union already contains every tool in the LIVE surface of this
  session, incl. apply_pending/persona_*/canonical trio/remember_media/
  task_progress/resolve_conflicts-style extras).
- tests/mnemo2_vendor_tools_wildcard.txt = 42 names = that union + spec §B1
  names (adds mnemosyne_resolve_conflicts, the #40 integration-registered
  tool not present in any manifest).
- tests/mnemo2_vendor_tables.txt = 36 table names from the vendor CREATE TABLE
  wildcard grep (`grep -rhoE 'CREATE[[:space:]]+(UNLOGGED |TEMPORARY )?TABLE
  (IF NOT EXISTS)? (ONLY )?[a-zA-Z_]' mnemosyne --include='*.py'
  --exclude-dir=tests --exclude-dir=__pycache__ | grep -oiE
  '[a-z_][a-z0-9_]*$'`, artifacts IF/below/_me_rebuilt removed) at the pinned
  vendor commit.

mnemo-pg2 implements all 42 tools with vendor-boundary contracts (same names,
JSON in/out). Deep vendor sub-mechanisms that are simplified MUST say so
explicitly in the result (phase-2 honesty rule, binding for R4).
Toolset registered with the host: `memory` (vendor parity —
integrations/hermes/src/mnemosyne_hermes/__init__.py register() uses
toolset="memory").

## 3. Host routes

mnemo-pg2 registers through the standard PLUGIN path (register(ctx)):
ctx.register_tool per tool (file-anchored schemas) + ctx.register_hook for
`on_session_start` and `pre_llm_call` (renders the injected "Mnemosyne
Memory"-style block from recall prefetch; no-op-safe).

The vendor ALSO exposes a memory-provider route (`register_memory_provider`,
agent/memory_provider.py MemoryProvider ABC: name/is_available/initialize/
get_tool_schemas/handle_tool_call). mnemo2agent uses `memory.provider:
mnemosyne` (vendor plugin), so mnemo-pg2's plugin-route tools ARE that
agent's live tool surface (Phase C probe proves dispatch inside mnemo2agent,
R7). A provider-route bridge class (get_tool_schemas/handle_tool_call on the
same engine, no host imports) ships in provider.py and is unit-tested against
the ABC contract WITHOUT importing host modules; live provider-slot takeover
is explicitly OUT of Phase-3 scope (R10: don't disturb existing agents'
memory stacks; documented state, not assumed).

## 4. Config resolution (R5, plan §4)

- DSN: env MNEMO2_PG_DSN > compose MNEMO2_PG_HOST/PORT/USER (defaults
  ~/lcm2-mnemo2-pg/pg/run, 54331, mnemo201) > dbname=mnemo2db.
- Global params from /home/mybuddy/.hermes/.env (host loads it); profile
  override from the profile .env (profile wins — proven by tests).
- Private schema: MNEMO2_SCHEMA override else deterministic
  `mnemo2_<agent>` derivation from HERMES_HOME/profile dir name (same
  schema_for_profile pattern as lcm2-pg pgconn.agent_schema — no pinned
  profile lists).
- Shared schema: MNEMO2_SHARED_SCHEMA, default `shared`.
- Embeddings: MNEMO2_EMBED_* (MODEL/DIM/API_URL/API_KEY); default =
  deterministic hashed-embedding in pure Python (dim 256) so recall works
  with zero network; API path identical shape when configured.
- Weights: recall vec/fts/importance default 0.5/0.3/0.2 (vendor defaults),
  per-query overrides like vendor.

## 5. Storage (R3: native PG, zero sqlite)

Per-schema tables (names = vendor wildcard list §2; the full 36 where they
hold state; a few vendor-only internals (harmonic_beliefs, media_*, cost_*)
exist as tables with minimal CRUD where their tool needs them and are
declared simplified elsewhere):
- cores: working_memory, episodic_memory, memories (vendor legacy dual-write
  parity), memory_embeddings (vector(256) via pgvector, not blob).
- FTS: search tsvector GENERATED columns + GIN indexes on working/episodic/
  canonical/persona (replaces FTS5 content-table + sync triggers entirely —
  zero CREATE TRIGGER in mnemo-pg2, R3-friendly).
- facts/graph: triples (valid_from/valid_until timestamptz; supersede closes
  prior s+p), graph_edges (bitemporal columns, valid_until = edge close;
  RECURSIVE-CTE BFS with visited-array cycle guard), memoria_facts,
  annotations, statements, facts, gists.
- canonical: canonical_facts UNIQUE(category,name) partial-where-open +
  supersede chain (retire = stamp, never delete).
- persona: memoria_persona (+tier CASE order), memoria_preferences
  (demote target), consolidation_log, statements.
- ops: scratchpad, audit_log, memory_validations (3-entry ring jsonb),
  memory_events + sync_memory_state + sync_meta + sync_outbox_ack (event-log
  sync contract; push/pull to REMOTE servers is declared network-simplified:
  pull applies from a local journal; documented per honesty rule),
  query_cache, conflicts + conflict_probe_pending (resolve_conflicts needs
  MNEMO2_CONFLICT_RESOLUTION=1 like vendor CROSS_SESSION flag),
  consolidation_log, meta, recall_diagnostics counters.
- shared schema: same working/embeddings/fts shape (minimal: shared_* tools
  only need working_memory + embeddings + canonical read), plus
  media_assets/media_moments ONLY in private schemas.
- vec: NO vec0 / sqlite_vec anywhere; pgvector cosine `<=>` + recall CTE
  (cosine + ts_rank + importance + temporal decay in one SQL pass, weights as
  params).

## 6. Tools → implementation map (vendor-boundary semantics kept)

remember (BEAM-lite write path: insert + embed + optional triple/entity
fan-out + extract flags; scope session/global; valid_until; veracity),
recall (ranked CTE, temporal decay halflife param, explain trace,
per-tier fallback chain documented), stats, triple_add/query/end
(supersede semantics, as_of), sleep (working→episodic set-based
consolidation with advisory lock + LLM optional, dry_run/all_sessions/force),
scratchpad trio, invalidate (replacement chain, bank routes surface→shared),
export (json_build SELECT), import (file JSON upsert; provider fetch
network-simplified — declared), update (re-embed explicit), forget,
diagnose (pg_catalog checks + vec_working-parity coverage repair),
graph_query (RECURSIVE CTE), graph_link (upsert pair+type), sync trio
(local-journal contract, network-simplified — declared), batch (ONE PG
transaction, ≤50 ops, rollback — native strength), get, validate (ring
buffer, any-agent attestation), canonical trio (slot uniqueness,
no-op-on-same-body), recall_canonical/forget_canonical, remember_media
(documented simplified: registers ref + text memory, no vision models),
persona quartet (tier CASE order, reinforcement bumps), shared quartet
(write/read/forget/stats against `shared` — THE cross-agent surface, R5),
model_card (deterministic SQL render), model_refresh (diagnostic list of
proposal rows), task_progress (canonical category 'task:progress'),
recall_diagnostics (counters table), resolve_conflicts (conflicts table +
supersede, env-gated), apply_pending (staged JSONL in
~/.hermes mnemo2-pending dir, YAGNI-simple; default off like vendor
write_approval), shared_stats (counts in shared).

Hooks: on_session_start (schema bootstrap + warm), pre_llm_call (context
block: top-N global recalls, env-gated MNEMO2_PREFETCH=0 disables),
post_tool_call (audit_log row for write tools). vendor manifest note:
hermes_memory_provider manifest lists exactly these three hooks
(pre_llm_call, on_session_start, post_tool_call — spec §B2), verified by cat
at the pinned vendor commit.

## 7. Import discipline (phase-2 (a)/(b) verbatim)

plugins/mnemo-pg2/_selfimport.py is a byte-mirror of lcm2-pg's proven
anchor/finder machinery with mnemo-specific name sets (HOST_BARE,
NEVER_PUBLISH, depth registry key `mnemo2_depth_*`, package alias
`_mnemo_pg2_pkg`). Files anchor() their siblings by __file__ under bare+
qualified names ONLY inside import windows; late calls resolve via the same
package-alias scan pattern. No bare sys.modules name is published after
register() completes.

## 8. Tests (TESTS FIRST — R11: this commit + the tests commit precede any
implementation commit in git history)

tests/mnemo2/ (conftest against the REAL instance on :54331, throwaway
schemas mnemo2_test_<uuid> + a shared sandbox, suite-start sweep carry):
- test_surface.py: manifest parity (42 wildcard names, no extra/missing),
  tool call dispatch returns JSON for every tool, schema shapes.
- test_r3_no_sqlite.py: zero sqlite refs in shipped code (grep + AST import
  scan), force-fail a PG op → error surfaces, no fallback (R3).
- test_canonical.py / test_triples.py / test_graph.py / test_batch.py /
  test_persona.py / test_scratchpad.py / test_ops.py: per-tool round-trip
  row-level assertions (DB rows cited, not just tool JSON).
- test_shared_cross_agent.py: R5 — agent A writes via shared_remember,
  agents A and B (two private schemas) both read it via shared_recall;
  private isolation both ways.
- test_r7_live_ops.py: remember/recall/stats round-trip in a mnemo2agent-
  shaped env (HERMES_HOME pinned) with DB-row citations.
- test_r8_concurrency.py: N parallel writers (threads) into two schemas +
  shared; no lost writes, batch atomicity under contention.
- test_recall_quality.py: hybrid ranking sanity (fts hit, vec hit, weights
  honoured, explain trace).
- test_config_resolution.py: DSN/schema resolution incl. profile-.env-style
  override precedence.

## 9. Non-goals (R10 discipline)

No changes to existing agents' mnemo-pg/lcm-pg stacks; no fleet rollout; no
migration of vendor data; no network sync servers; no vision/media model
wiring. Live proof C runs on mnemo2agent + lcm2agent only.
