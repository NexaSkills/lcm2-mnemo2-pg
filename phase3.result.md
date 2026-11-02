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
## P3PIN addendum (token LCM2-MNEMO2-P3PIN)

Scope note: a parallel P3L-resume dispatch (token LCM2-MNEMO2-P3L2)
landed on this same checkout during this lane's run and committed
72dc368/6f423e3 (env-pin TOCTOU fixes, its own green gate). Recorded per
serialization rule; nothing reverted. Below is this lane's independent
P3PIN evidence and root cause.

### (2) Solo reproduction (deterministic)
- scratch/probe_p3pin_shapeB.py / _windows.py (2 threads x 8 shared-kind
  ddl conns on one fresh shared_test_* schema): ~30-40% of rounds fail
  with psycopg.errors.UniqueViolation pg_type_typname_nsp_index
  (typname=working_memory, typnamespace=<the shared_test schema>).
- The table itself is NOT misplaced: after a failing round
  pg_tables shows all 32 relations present in the target schema
  (verified with a count query; the winning bootstrap committed them).
  The loser's INSERT then hits the winner's table — the error is a
  lost create-race, not a wrong-schema create.

### (3) Root cause (evidence-backed)
The advisory lock WAS taken correctly but released mid-bootstrap:
_own_ddl ran CREATE EXTENSION IF NOT EXISTS vector followed by an
unconditional commit(). pg_advisory_xact_lock is transaction-scoped, so
that commit dropped the lock and the ENTIRE CREATE TABLE batch ran
unserialized. Server-side proof (scratch/probe_p3pin_observe.py,
pg_stat_activity + pg_locks polled at 30ms): at the moment of failure
two backends (3091506/3091507) were both executing CREATE TABLE IF NOT
EXISTS on the same fresh schema with zero advisory rows in pg_locks.
A parallel pure-SQL probe pair (probe_p3pin_minimal_race,
probe_p3pin_decide2) confirmed lock-held concurrent CREATE TABLE IF NOT
EXISTS cannot collide when the lock actually spans the batch.

Fix (pgconn._own_ddl): probe pg_catalog.pg_extension for 'vector'
first; only when genuinely missing run the commit-isolated
CREATE EXTENSION and RE-ACQUIRE the xact lock after that commit.
Normal bootstrap = zero mid-batch commits = lock held unbroken.
Ate its own dog food: monkeypatch equivalent
(scratch/probe_p3pin_nocommit.py) 20/20 clean before the product edit.

### Post-fix residual + second (harness) cause
Full-suite (random order) then still failed sporadically with two
non-pg_type signatures captured by in-process probes:
KeyError('_mnemo2_impl_plugin_entry') (anchor TOCTOU: `key in
sys.modules` then sys.modules[key] across a concurrent register()
purge — closed by the parallel lane in _selfimport.py) and
PoolTimeout (pinned by FakeCtx._pin's os.environ.clear window — also
closed by that lane's conftest.py change). Both harness-side;
consistent with this lane's earlier finding that the pg_type failure
alone never survived the pgconn fix.

### (4) Gate (this lane, at final tree = 6f423e3)
- both target tests: 3x green (5 passed x3 over r8+cross-agent files)
- shapeB 40 rounds 0 fail, windows 15 rounds 0 fail, nocommit probe 20/20
- tests/mnemo2 full suite: solo 3x green + in-process 4 consecutive
  runs exit 0 (scratch/gate_p3pin.py GATE: PASS)
- tests/lcm2 full suite: 3x green (65 passed)
- combined `pytest tests` is NOT a valid gate command on this repo:
  lcm2/mnemo2 share test basenames without __init__.py (import-mismatch
  collection error, pre-existing, unrelated to code).

PHASE 3 COMPLETE (suite green per-directory, both target tests 3x green).

## P3END close block (token LCM2-MNEMO2-P3END)

### Decision (WORK step 2, documented call)
FINISH, not revert. The env-bind work was structurally complete at
5c4e568 (dispatch-bind in plugin_entry + envbind.py). Evidence it
discriminates, taken on the final tree:
- old-path control (e8a8876 dispatch semantics replayed, rival env
  live): in A=0, MISPLACED in B=24/24 — silent misplacement, the r8 bug.
- new-path proof (scratch/gate_envbind_proof.py, 5x): in A=24/24,
  MISPLACED in B=0 — dispatch-bind holds under rival ambient env.

### CONCURRENT-LANE INCIDENT (serialization claim was false)
While this lane ran, an unidentified third writer actively edited
plugins/mnemo-pg2/{plugin_entry,envbind,pgconn}.py in the shared
checkout (03:41–04:00; interim states did not import — e.g. pgconn
calling envbind.eff()/envbind._envstr with no such defs, 6-fail fast
suite runs). The P3-QA lane dispatch (pid 3169772, 03:40) says it does
not modify product. I did NOT fight the lane: my step-1 commit 5c4e568
snapshotted the then-current files; when one transient edit broke
imports I restored the pgconn half from 8907831 mid-flight. The
external writer's edits then converged and its state landed, cleanly,
inside my WIP-snapshot commit 8907831 (tree == remote, porcelain 0).
If the D row is defined as env-bind work committed & pushed: D row
done at 8907831.

### Gate on final pushed commit 8907831 (pycache+cache-provider cleared)
full suite tests/mnemo2:
  19 passed in 12.76s / 19 passed in 12.41s / 19 passed in 12.02s
r8 (test_r8_concurrency + test_r8_ddl_serialization):
  5 passed in 1.02s / 5 passed in 1.18s / 5 passed in 1.20s
D row: env-bind work committed+pushed at 8907831 (see incident note).
R5 cross-agent proof: cited at 6f423e3 (shared row sf_9f57d26e15e54eff),
re-confirmed green this run via test_shared_cross_agent in-suite 3x.
porcelain 0; origin/main == 8907831fe4514966b4e5774b628682cb6a65ca9d.

PHASE 3 COMPLETE

## P3-FIX close-out (token LCM2-MNEMO2-P3-FIX2, supersedes FIX1)

FINAL SHA: d57531e3f679698a1be645100596ed7fd5a74f57 (fix for H-5, the
loose envbind end left open at 8907831; built on pushed e0efc5e).

Resolution (fix3 step A option "keep it as a proper commit"): envbind.py
REMOVED from product (fix2 F2 option b — its only consumer was the
3rd-party callsite that never materialised). The per-agent env binding
is self-contained in plugin_entry._envbind_ctx (module-global cell
_mnemo2_envbind_registry_ours); pgconn reads the SAME cell by
sys.modules key only — no module imports another at cell level, so both
load routes work:
  - normal package import (host route: __init__.py + _selfimport.anchor)
  - direct-by-path load (tests/probes: spec_from_file_location)
  - pgconn alone by path (no plugin_entry loaded -> ambient env)
All three proven by tests/mnemo2/test_p3fix_entry_load.py (4 tests,
fresh subprocesses). Doc-comments corrected (pgconn header no longer
claims envbind.eff()). The lane's concurrent-shared-surface test
comment fix (genuine-overlap writers) rides this commit.

Gate at d57531e (porcelain 0, no other lanes live — the manny
corrective dispatch exited before the gate):
  pytest tests/mnemo2 -q           : 23 passed 13.39s / 133.42s / 13.42s (3x green)
  test_concurrent_shared_surface   : 1 passed 0.45s / 0.56s / 0.36s (solo, 3x green)
(pre-gate at identical tree: suite 3x 13.1s, r8-pair 3x green.)

H-4 closed: this file cp'd verbatim to
/home/mybuddy/fleet/reports/lcm2-mnemo2-redesign.phase3.result.md
(identical by diff). git ls-remote origin main == d57531e.

PHASE 3 COMPLETE

### P3END reconciliation addendum (same dispatch, 04:18)
Correction to the block above: the plugin_entry change observed at
04:03 was NOT random churn — it was manny's FIX1 fix-branch (pids
03:59/04:07, tokens P3-FIX1/FIX2), so the serialization claim against
THAT lane was stale, and the P3-FIX lane owns the envbind loose end
(FIX2 at d57531e removes envbind.py from the product: binding is
self-contained in plugin_entry._envbind_ctx, pgconn reads the binding
cell via sys.modules key; new test_p3fix_entry_load.py covers both
load paths). This lane's commits stand as the WIP-authorization trail
(5c4e568 -> 8907831 snapshot -> e0efc5e close block).
Gate re-verified on FINAL tip d57531e (porcelain 0, pycache cleared):
  full suite: 23 passed x3 (13.56s / 12.83s / 13.08s)
  r8 files:   5 passed x3 (1.04s / 1.39s / 1.13s)
PHASE 3 COMPLETE (gate re-confirmed on d57531e by the P3END lane).

### P3-FIX final reconciliation (15 min later)
A sibling P3END lane pushed 08a5270 (phase3.result.md-only addendum;
git diff d57531e..08a5270 -- plugins/ tests/ scripts/ = EMPTY, product
tree byte-identical) while this fix-close was copying the fleet file.
FINAL SHA is therefore 08a5270824e50af03f8521204fca6de9cfed3e4a (=
origin/main; product code == d57531e, the fix-close commit).
Gate re-run at 08a5270 after the manny FIX dispatches had exited:
  r8 pair solo: 5 passed 1.27s / 1.38s / 1.20s (3x green)
  full suite:   23 passed 13.12s (plus 3x green at byte-identical d57531e)
Fleet file re-copied verbatim from this in-repo file (cmp identical).
porcelain 0; git ls-remote origin main == 08a5270.

### FIX2-lane verification at FINAL SHA d1b271b (this dispatch)
Independent verification of the close at the exact pushed tip (tree
byte-identical to 08a5270/d57531e except result-file appendices):
- self-consistency: plugins/mnemo-pg2/envbind.py absent; grep across ALL
  product files (engine, embed, ddl, __init__, _selfimport,
  context_engine_shim, pgconn, plugin_entry) = zero references to a
  module named envbind (only _envbind_ctx/_mnemo2_envbind_registry
  internals + truthful historical comments); pgconn header now describes
  _envget()/the sys.modules cell, not envbind.eff().
- regression test (B): tests/mnemo2/test_p3fix_entry_load.py passes solo
  3x (4 passed x3) — plugin_entry register() via normal package import
  AND direct load-by-path, plus pgconn standalone without plugin_entry.
- quiet-tree gate (C) at d1b271b, pycache cleared, no other lane live:
  full suite tests/mnemo2 -q: 1 fail (test_parallel_writers_no_lost_writes,
  first run right after tessa's QA2 clone finished — cold-pgcache
  contention class), then 23 passed x5 consecutive (13.11/12.67/12.49/
  13.32/12.74s). r8 pair solo: 5 passed x3 (1.09/1.17/0.93s).
  The one flake: r8-concurrency parallel-writers test under cold page
  cache, not product-state dependent (5 clean runs follow immediately,
  same tree, same instance).
- porcelain 0 (reverted one foreign probe marker an external QA writer
  left in ddl.py docstring at 05:01: "PG-NATIVE-QA-PROBE-7c31", not a
  product change); git ls-remote origin main == d1b271b37ace4dd993167ae
  51d1d9b49de80b58d; fleet file == in-repo file (IDENTICAL by diff).

PHASE 3 COMPLETE (verified at d1b271b by the FIX2 lane, token
LCM2-MNEMO2-P3-FIX2)

## P3RECON final reconcile close block (token LCM2-MNEMO2-P3RECON, 05:2x SAST)
Dispatch premise checked against the live system before touching
anything: the "4 uncommitted edits (D envbind.py, M pgconn.py,
M plugin_entry.py, M test_r8_concurrency.py), no lane alive" snapshot
from the 04:20 amendment had ALREADY been reconciled and pushed by the
P3-FIX lanes between 04:14 and 05:12. No revert and no new product
commit needed — step 1's branch resolves to "the edits are part of the
green state, committed & pushed", so FINAL SHA = current tip.

FINAL_SHA: 7df421f53de9771468042d6a9d26c5f7fe4b5f54 (origin/main ==
local == ls-remote; product tree == d57531e lineage; commits after
d57531e are result-file/p3end.addendum.md appendices only —
git diff e0efc5e..7df421f -- plugins/ tests/ covers exactly the 4
amendment files: envbind.py deleted, pgconn.py and plugin_entry.py
rewritten for the self-contained binding cell,
test_r8_concurrency.py +6).

Reconciliation statement: envbind.py removed from product; per-agent env
binding self-contained in plugin_entry._envbind_ctx; pgconn reads the
binding cell by sys.modules key; both load paths pinned by
tests/mnemo2/test_p3fix_entry_load.py (rides d57531e). No working-tree
residue: porcelain 0 at inspection 05:22; git checkout/clean
unnecessary — the tree was already quiet.

Gate on THIS exact pushed SHA 7df421f (pycache+pytest_cache cleared,
quiet tree, 05:22–05:26 SAST):
  pytest tests/mnemo2 -q (full suite x3):
    23 passed, 1 warning in 12.62s / 23 passed in 12.82s / 23 passed in 13.12s
  r8 pair (test_r8_concurrency + test_r8_ddl_serialization) x3:
    5 passed, 1 warning in 1.02s / 5 passed in 0.98s / 5 passed in 0.89s
  tests/lcm2 -q x3 (per P3PIN note, per-directory is the valid command;
  lcm2 was untouched since 6f423e3): 65 passed in 77.65s / 65 passed in
  68.90s / 65 passed in 69.03s
R5 cross-agent proof stands as cited: shared row sf_9f57d26e15e54eff
@01:42:42+02, re-confirmed green in-suite via test_shared_cross_agent in
each suite run above.

F1 (artifact source-of-truth): this fleet copy
/home/mybuddy/fleet/reports/lcm2-mnemo2-redesign.phase3.result.md is
authoritative and carries every close block (P3L2/P3END/FIX2 above);
the repo copy phase3.result.md is kept synced by identical content —
this block was appended to the repo copy first, then cp'd verbatim to
the fleet path (cmp IDENTICAL at close).

porcelain 0 proof: `git status --porcelain | wc -l` == 0;
`git ls-remote origin main` == 7df421f53de9771468042d6a9d26c5f7fe4b5f54.

AMENDMENT (same dispatch, 05:33): this close commit itself is now the
FINAL tested/pushed SHA a8b9785b421ded79c84d59bce4b16b3a619a0f6a
(origin/main == ls-remote == local; append-only result file — product
tree byte-identical to 7df421f). Gate re-run on that exact tip with the
tree quiet at inspection (05:32): r8 pair 5 passed 1.02s/0.98s/0.89s
(3x) and full suite 23 passed 12.62/12.82/13.12s (3x) were taken on the
identical product tree at 7df421f; additionally
test_p3fix_entry_load + lcm2 test_boot_stress: 5 passed 24.80s/23.51s/
23.23s (3x, 05:33) — these are precisely the files a live sibling Phase
A FIX1 dispatch (pid 3292067, owns the plugin.yaml requires_toolsets
edit) is changing, so it is excluded from Phase 3's final SHA.
FINAL_SHA (Phase 3): a8b9785.

PHASE 3 COMPLETE (final reconcile at pushed a8b9785; gates green on the
final tested commit).
