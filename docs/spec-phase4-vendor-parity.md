# mnemo-pg2 Phase 4 — vendor-behaviour parity spec

Slug: lcm2-mnemo2-redesign · Phase: 4 · Written 2026-11-05 (mike)
Vendor ground truth: github.com/mnemosyne-oss/mnemosyne @ 6aefa4f (installed
build 0.7.3, catalog pin d738487 — behavior identical in the loops audited),
plus the retained sqlite original's pyc manifest. Every vendor claim below was
read from source, not from docs.

## 1. Vendor behaviour inventory (verified, with file:line)

### 1.1 Auto-recall injection — `prefetch()` (vendor __init__.py:2455) + core/prefetch.py
- Runs EVERY turn on the user message; no agent instruction needed.
- Budgeted: OVERFETCH=16 recalled then junk-filtered and capped; stopword
  lists for dedup (model-slot tokens, fragments < 8 chars, excluded/raw
  prefixes, distilled sources = canonical/persona prefered over raw).
- Our mnemo-pg2: MATCHES the loop (both injection paths — provider
  `prefetch` + plugin_entry `_pre_llm_call`), hybrid-scored, now includes the
  shared bank (2026-11-05). GAPS: no overfetch+filter (we recall exactly
  limit rows; short/garbage rows can be injected), no distilled-source
  preference (canonical/persona rows should beat raw working rows).

### 1.2 Auto-capture — `sync_turn()` (vendor __init__.py:2665)
- THE learning loop: every turn writes user and assistant content into
  episodic/working memory via beam.remember with [USER]/[ASSISTANT] prefixes.
- Guards that make it safe (this is the part to copy, not the raw write):
  - `write_policy` from core/filters (resolve + per-session overrides) —
    capture can be globally/on-session tuned off without code change;
  - content_sanitizer + chat_normalize strip machine envelopes (cron
    headers/replies, background-process notices) — never stores system noise;
  - role filter (`_sync_roles`), min length > 5 chars, user-size cap;
  - verbatim_ledger.begin/capture = idempotency tickets (a retried turn
    cannot double-store);
  - skip_contexts (cron/child agents don't pollute the brain);
  - telemetry (in-flight, queue length) around the write.
- Our mnemo-pg2: NO-OP BY DECISION (F-4 "unfiltered chat" concern,
  memory_provider.py:286 comment). GAP = the whole vendor capture lane; the
  F-4 concern is answered by the vendor's own guards above (sanitizer +
  policy + pending-queue, not by refusing to capture).

### 1.3 Consolidation ("sleep") — `on_session_end()` (vendor __init__.py:4717) → beam.sleep() (beam.py:12981)
- Fires at session end (threaded, 15s default timeout
  MNEMOSYNE_SESSION_END_TIMEOUT, reflection-BUDGET reservation so chatty
  sessions cannot run it every /new).
- sleep() = batch old working_memory rows → episodic summary via LOCAL LLM,
  aaak compression fallback when no model; source rows NOT deleted — marked
  consolidated_at and stay recallable (lossless-preserving).
- sleep_all_sessions() = maintenance sweep for old inactive sessions.
- fleet_conflict_census runs every sleep pass (cross-bank conflicts).
- Our mnemo-pg2: sleep EXISTS as a tool (mnemosyne_sleep) but NOTHING calls
  it automatically and consolidation_summary/consolidated_at semantics are
  partial. GAP = session-end + scheduled invocation + budget guard.

### 1.4 Reinforcement on recall — beam.py:7298/7349
- Every recall bumps recall_count + last_recalled on RETURNED rows (single
  batch UPDATE, capped per call); persona rows carry reinforcement_count
  (persona_reinforce = counter bump; promote/demote by count).
- Scoring feeds on recency+importance; the counters ARE the reinforcement
  signal that later consolidation/promotion reads.
- Our mnemo-pg2: persona_reinforce exists (engine.py:688) but recall() does
  NOT touch recall_count/last_recalled; scoring ignores them. GAP = one
  batch UPDATE at the end of recall() + optional score term.

### 1.5 LLM extraction — extraction/ (client+prompts), _extract_and_store_facts/entities inside remember()
- remember(extract_entities=True) → regex entities (idempotent); LLM
  fact-extraction feeds the facts table (provenance=DERIVED via "extraction"
  kind). Pending/validation lane: extractions land as CANDIDATES the
  apply_pending/resolve_conflicts tools adjudicate — this is how the vendor
  auto-capture avoids polluting recall with junk.
- Our mnemo-pg2: pending + validate + resolve_conflicts tables/tools exist
  and are proven; the LLM extraction call itself is not wired into
  remember/sync_turn. GAP = extraction client (pointed at the LiteLLM proxy
  `aux` alias — NEVER a vendor-local model; fleet rule) + DERIVED provenance.

## 2. PG-native mapping (sqlite → ours)
| vendor (sqlite) | mnemo-pg2 (PG) |
|---|---|
| per-beam sqlite file + WAL | mnemo2_<agent> schemas, ONE cluster :54331 (already) |
| recall_count/last_recalled cols (ALTER ADD) | ADD COLUMN IF NOT EXISTS to working/episodic via ddl.py lazy path |
| consolidated_at marker | same column; partial index WHERE consolidated_at IS NULL |
| verbatim ledger (session tickets) | mnemo2_ledger table (session_id, turn_hash UNIQUE) — INSERT ON CONFLICT DO NOTHING = idempotency |
| threading + sqlite locks | keep hooks async-safe via existing _scoped/_sibling guards; PG handles concurrency |
| local_llm / aaak fallback | LiteLLM proxy `aux` alias (proxy rule); aaak port optional — fallback MAY stay summarise-if-LLM-present else skip (YAGNI: no second compressor) |
| BM25 FTS5 | pg_trgm/FTS (already) |

## 3. Phases (each independently shippable, env-gated, test-first)

P4-A REINFORCEMENT (smallest, no new failure modes)
- recall() end: single UPDATE ... FROM (VALUES ...) SET recall_count=
  recall_count+1, last_recalled=now() for returned ids; expose
  recall_count as a small score term (default weight 0 — parity of DATA
  first, behaviour tuning later).
- clone-shape test: recall twice → counter +2; live test: probe row recalled
  in two sessions shows count.
- Kill-switch: MNEMO2_RECALL_REINFORCE=0.

P4-B AUTO-CAPTURE (the learning lane)
- sync_turn(user, assistant, session_id, messages): sanitizer (port vendor
  filters.py envelope-stripping regexes ~60 lines) → write_policy check →
  ledger ticket → remember(..., source='auto', importance=0.3,
  status='pending') into working tier ONLY (auto rows NEVER jump to
  canonical; validation promotes or deletes them).
- Roles/length/size caps + skip-contexts (cron lanes excluded) +
  MNEMO2_AUTO_CAPTURE=1 default OFF for transition, then default ON.
- Tests: sanitizer table-driven (cron/bg/MCP envelopes), ticket idempotency
  (retried turn = 1 row), pending rows excluded from canonical recall but
  recallable in working tier.

P4-C CONSOLIDATION
- Wire sleep() semantics to vendor: select working rows WHERE
  consolidated_at IS NULL AND age > cutoff AND source IN (auto,user) →
  LLM (aux alias) episodic summary → INSERT episodic + mark consolidated_at
  (originals kept — lossless invariant).
- Invokers: on_session_end (threaded, 15s cap, reflection budget: max 1
  sleep/session, ≥ N turns since last) + systemd-user timer
    mnemo2-consolidate.timer (pattern: ensure.timer) running
    scripts/consolidate_session.sh <profile> for stale sessions.
- Tests: clone-shape with stubbed LLM (summary written, markers set,
  originals recallable); live: session with >cutoff rows → sleep → delta.

P4-D INJECTION POLISH
- Overfetch 16→filter(stopwords/fragments)→cap 8; distilled-source ordering
  (canonical/persona before raw working); shared-section dedupe already in.
- Budget: block ≤ 1200 tokens total (parity knob, env-tunable).

P4-E PERSONA LOOP (last; only if Jan wants agent "personality memory")
- promote/demote thresholds off reinforcement_count + recall_count joins;
  sleep() folds persona refresh into the same pass (vendor does this in
  sleep via reflection).

## 4. Standing constraints (non-negotiable, fleet rules)
- No Hermes code touched; all changes repo-side (symlink lane = live on next
  session; surfaces need the usual bounce).
- Every new behaviour env-gated with parity default; kill switches per phase.
- LLM calls ONLY via LiteLLM proxy aux alias; zero cloud otherwise.
- Lossless invariant: capture/consolidation NEVER deletes source rows
  (consolidated_at marker only) — same contract as lcm2-pg thrash-cure.
- Shared bank stays tool-lane + the small injected slice (done 2026-11-05);
  cross-host sync stays dormant (separate decision).
- Rollout = existing 3-verb pattern (pull → apply_surface_fixes → bounce);
  beez first, prove with live 4-leg + new capture/consolidation legs.

## 5. Effort/order estimate
A: half-day (code+tests+beez proof). B: day (sanitizer fidelity is the risk
— vendor regexes ported verbatim, not paraphrased). C: day (LLM prompt via
aux + budget guard). D: quarter-day. E: later/optional. Total ≈ 3 days of
focused work, each phase separately deployable and revertable.
