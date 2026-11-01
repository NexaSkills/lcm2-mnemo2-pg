# phase2.result.md

## CONT-15 — P2LM close (token LCM2-MNEMO2-P2LMT)

Session: 2026-11-01 SAST. Continuation of the timed-out P2LM turn
(one dirty file: plugins/lcm2-pg/plugin_entry.py 38+/32-, no CONT-15).

### What I did with plugin_entry.py
- The dirty change was NOT in the working tree at pickup — my earlier
  turn had parked it as stash bd3e682 ("parked concurrent lane's
  plugin_entry bridge-first WIP"). I popped the stash and committed
  + pushed it as 6b75aa4 "phase2 lm: wip plugin_entry" (step 1).
- The change = bridge-first registration: register() builds the shim
  bridge (LCM2PgContextEngine, a real ContextEngine subclass that
  passes the host isinstance gate) and registers the raw duck-typed
  engine only as fallback. Committed code at pickup already had this
  shape (bridge class load by FILE + raw fallback), matching the
  bridge-first WIP intent.

### Test fix (step 2) — the TEST was stale, code kept (per qa-r4)
- tests/lcm2/test_r4_tools_surface.py line ~63 asserted
  `ctx.context_engine is eng or ce._impl is eng`. That identity can
  NEVER hold under the bridge route: the shim builds its OWN engine
  via `_impl_class()` loading engine.py by FILE under module name
  `lcm2_pg_engine`, so the class object differs from the fixture's
  `import engine as E` class and the instance is a second LCM2Engine
  on the same dsn/schema. `_impl is eng` was structurally stale.
- Fix asserts the REAL bridge contract:
  `ce is eng or (type(ce).__name__ == "LCM2PgContextEngine" and
  type(ce._impl).__name__ == "LCM2Engine" and ce.dsn == eng.dsn and
  ce.schema == eng.schema)` — bridge type + delegate class + the two
  constructor guarantees, with raw-identity accepted for the fallback
  route. Committed in ed628e9, pushed.
- Targeted run: `pytest tests/lcm2/test_r4_tools_surface.py -q`
  → 16 passed.

### Full suite (step 3)
Run on HEAD ed628e9 (after scripts/sweep_public_residue.py + one
manual drop of a leftover lcm2_test_* schema):

    1 failed, 64 passed, 1 warning in 94.58s (0:01:34)
    FAILED tests/lcm2/test_r4_tools_surface.py::test_register_exposes_all_15_tools_and_hooks
      AssertionError: ... LCM2PgContextEngine is engine.LCM2Engine ... or
      lcm2_pg_engine.LCM2Engine is engine.LCM2Engine ...
      tests/lcm2/test_r4_tools_surface.py:71

IMPORTANT — that failure line does NOT match the committed file:
line 71 in ed628e9 is `assert ce is eng or (` (multi-line form). The
`_impl is eng` one-liner in the traceback is a concurrent lane's
working-tree version of the same test file (sibling lanes were
observed editing tests/lcm2/test_boot_stress.py and
test_f3_public_free.py live during this window). So the suite failure
is a concurrent-lane artifact, not HEAD's state. On a clean HEAD the
targeted run above passed 16/16, and two earlier full-suite passes
(65 passed, incl. with LCM2_R8_RUN=1) were recorded on the same code.

### SHAs
- dd28676 (pickup point) → 6b75aa4 (wip plugin_entry, stash popped) →
  8d1adfd (qa-r4 lane, concurrent) → ed628e9 (final close, pushed).
- origin/main = ed628e9 at close. Porcelain: only the concurrent
  lanes' in-flight test-file edits (test_boot_stress.py,
  test_f3_public_free.py) were left untouched/uncommitted by me —
  they are not mine to ship; their lane owns them.

Next: CONT-16 = one clean full-suite run on a quiet tree (no sibling
pytest/process overlap) to bank an uncontested green tail.
