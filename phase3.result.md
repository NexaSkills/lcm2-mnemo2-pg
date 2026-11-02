# phase3.result.md — LCM2-MNEMO2-P3X (close-out attempt, 2026-11-02 01:52–02:24 SAST)

STATUS: NOT fully green — work order step 3 executed (25-min fix window
expired). Suite state at stop: 18 passed / 1 failed
(tests/mnemo2/test_r8_concurrency.py::test_concurrent_shared_surface).
Everything red is committed (nothing uncommitted, porcelain 0 at push).

## Failure (exact)
- Test: test_concurrent_shared_surface (solo rerun reproduces, ~every run).
- Error: ['a died' or 'a#0': UniqueViolation: duplicate key value violates
  unique constraint "pg_type_typname_nsp_index"
  DETAIL: Key (typname, typnamespace)=(working_memory, <oid>) already exists.]
- Which writer dies varies (a or b); ddl-serialization sibling test
  (test_concurrent_different_schema_bootstraps_no_unique_violation) passes —
  the race is specific to the shared+private bootstrap OVERLAP, not the
  plain two-private-schema race 92d138d fixed.

## Root-cause picture (evidence-backed)
1. Advisory lock IS taken on every bootstrap (instrumented probe:
   scratch/probe_shared_surface.py shows adv_lock_held=True on both
   private and shared Conn entries). 92d138d + (dsn,dbname) keying landed.
2. _own_ddl() commits CREATE EXTENSION inside itself -> the xact-scoped
   advisory lock is RELEASED mid-bootstrap at that commit; the remaining
   CREATE TABLE IF NOT EXISTS batch then runs UNSERIALIZED.
   (7f888ee's comment documents this tradeoff deliberately: extension +
   CREATE TABLE in one txn trips pg_depend races.)
3. The unserialized remainder races on pg_type in at least one of these
   overlap classes:
   a) private Conn bakes shared_* CROSS-VIEW DO-blocks and shared-schema
      Conn bootstraps concurrently — view CREATE holds ACCESS EXCLUSIVE on
      shared-schema base tables, serializing against the shared bootstrap
      (sibling's 9ba7664 restructure moved the rebake under the lock —
      landed after my window; test_shared_visible_from_two_agents went
      green, r8 surface write still dies).
   b) unqualified CREATE TABLE IF NOT EXISTS resolves through search_path;
      a private (schema,public) and a shared (schema) Conn can resolve the
      SAME name into DIFFERENT namespaces -> both catcache-skip, both
      INSERT pg_type. (a31acd4 self-qualification closes this class.)
4. psycopg connection __exit__ COMMITS (not rollbacks) on clean exit, so
   the probe "hold lock across a sleep" test released it silently — lock
   isolation alone (18.6 has (int,int) and (bigint) advisory overloads;
   (bigint,bigint) does NOT exist, confirmed on the live instance) was
   never actually being held across the DDL batch in the probe either.

## Attempted diagnoses / fixes during window
- pgconn advisory lock fully pg_catalog-qualified (mine, committed in
  0ff33b4 lineage) — no behavior change (functions are in pg_catalog
  anyway; overload mismatch noted above is in the PROBE SQL, plugin SQL
  itself casts both args to bigint and silently falls into the
  UndefinedFunction rollback path?? NO — plugin call resolves
  (bigint,bigint)? NOT confirmed: instance pg_proc shows only (bigint)
  and (int,int) overloads. The plugin's
  pg_advisory_xact_lock(hashtext(%s)::bigint, hashtext(%s)::bigint)
  matches NEITHER overload -> UndefinedFunction -> swallowed by the
  except: rollback in __enter__ -> NO LOCK EVER TAKEN. ** PRIME SUSPECT,
  NOT FIXED IN WINDOW. **
  VERIFY FIRST in next session: SELECT pg_advisory_xact_lock(1::bigint,2::bigint)
  on mnemo2db — if UndefinedFunction, the whole 7f88ee2/92d138d advisory
  serialization was a no-op from the start and the correct minimal fix is
  pg_advisory_xact_lock(hashtext(%s)::bigint) — SINGLE-key (or the
  (int,int) overload WITHOUT the ::bigint casts).
- ddl.py: self_schema qualification of every CREATE TABLE (sibling lane
  landed a31acd4 variant; mine was equivalent).
- ddl.py: private shared_* views baked schema-qualified (my edit, landed
  inside sibling's 525aeef/9ba7664 lineage).
- _want_views AttributeError hardening (mine, 0ff33b4) then sibling init
  (9ba7664).
- Instrumented probes: scratch/probe_shared_surface.py (lock-held check),
  scratch/probe_trace.py (timing trace; died on sibling's transient
  broken state — kept for next session).

## Companion chaos note (honesty)
A sibling agent (api-cfecb2e747530314 / api-3dba7ab6274158bc) was live-
editing the SAME files (ddl.py, pgconn.py, conftest FakeCtx, r8 test)
through the whole window, committing under its own token
LCM2-MNEMO2-P3-CONT. Its edits and mine interleaved on disk; every wip
snapshot commit under P3X exists because the worktree was mid-edit by it.
The suite moved 16/17 -> 18/19 under the combined edits; the LAST
remaining failure is the one above.

## Gate status at stop
- Full suite: 18 passed, 1 failed (test_concurrent_shared_surface).
- r8 test 3x: 1 failed / 2 failed / 1 failed variants (never 3x green).
- porcelain: 0 after close commit.

## ADDENDUM (02:30 SAST) — advisory-overload fix landed (28583c1)
- Confirmed LIVE on mnemo2db: pg_advisory_xact_lock(bigint,bigint) =>
  UndefinedFunction (pg_proc 18.6: (bigint) and (int,int) overloads only).
  The 7f88ee2/92d138d serialization NEVER took a lock; the except:rollback
  in __enter__ hid it. Fixed: single-key
  pg_advisory_xact_lock(hashtext(dsn || 0x1f || dbname)::bigint).
- Post-fix r8 solo rerun: failure CHANGED character —
  UniqueViolation pg_type_typname_nsp_index => UndefinedTable
  "shared_test_x".working_memory does not exist on the surface INSERT
  (shared schema's tables never created for the Conn's target). Shared-
  kind bootstrap is now skipping/failing its CREATEs — suspect the
  sibling lane's ddl.py _qualify_refs/bake_view_statements restructure
  (14c7d95) interacting with kind='shared' self_schema, OR an extension
  pre-step now serialized differently. This is closer (fail-closed,
  deterministic, no catalog corruption), and the next diagnosis is short:
  run one shared-kind bootstrap alone and diff its CREATE list against
  ddl.statements('shared', schema).
- Commit state: 28583c1 pushed-ready; porcelain 0. NO 'PHASE 3 COMPLETE'
  (suite not fully green at close).

## PHASE 3 LAST ITEM — P3L resume checkpoint + close (token LCM2-MNEMO2-P3L2)
Timeline (session start 03:2x SAST): checkpoint commit at ~t+2min
(72dc368), full gate verified by t+8min, closed at ~t+10min (HARD STOP
20min respected; nothing further touched after green).

### What the in-flight diff does (72dc368, the 2 uncommitted files)
1. plugins/mnemo-pg2/_selfimport.py (+12): _anchor_locked no longer does
   `key in sys.modules` then `sys.modules[key]` — the two-step lets a
   concurrent register()'s purge commit its delitem between the C atomic
   ops => KeyError INSIDE anchor(). Now a single sys.modules.get() read,
   then object test. No architecture change; pure TOCTOU closure.
2. tests/mnemo2/conftest.py (+43): FakeCtx._pin and register_agent
   restore replaced os.environ.clear()+update() with set-only rebind +
   targeted pops (both apply and restore directions). Rival-thread
   snapshot reads (dict(os.environ)) landing inside a clear window were
   KeyErrors from <frozen os> — harness-manufactured race, not product
   race. Global env is now never emptied, only overwritten per-key.
Together with the already-committed 24e202c pgconn vector-probe fix,
this closes the last r8 failure chain (shared-surface bootstrap).

### Baseline BEFORE full-suite run (step 2, solo 3x)
test_concurrent_shared_surface alone:
  run 1: 1 passed, 1 warning in 0.50s
  run 2: 1 passed, 1 warning in 0.45s
  run 3: 1 passed, 1 warning in 0.48s

### Full-suite gate (step 3)
tests/mnemo2 full suite after in-flight diff:
  19 passed, 1 warning in 12.97s
repeat x3 (no randomization plugin present; -p no:randomly no-op):
  19 passed in 13.32s / 19 passed in 12.75s / 19 passed in 13.11s
r8 green 3x + full suite green => gate MET, no remaining failure.

### State
porcelain 0, pushed to origin/main.

PHASE 3 COMPLETE
