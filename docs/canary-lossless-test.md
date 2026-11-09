# Canary test — proving lcm2-pg is truly lossless after install

Purpose: after an install/rollout, prove the context engine is *lossless* —
that content pushed out of the live context by compression is still
recoverable **verbatim** from `lcm2db` via the recall tools. A passing run
is the strongest single post-install proof (stronger than `check_mnemo`
or boot-log lines, because it exercises the whole ingest → fold → recall
lane end to end with a real agent session).

This test needs a human in the loop (they watch the context bar and fire
the trigger message). It is a *prompt protocol*, not a shell script.

## Prerequisites (run before starting)
1. Engine check: `hermes -p <profile> config get context.engine` →
   `lcm2-pg`. Anything else = STOP, the session is not under lcm2-pg.
2. `LCM_CONTEXT_THRESHOLD` on the host (default 0.6; some hosts pin 0.75).
   Compression should fire near threshold×window (e.g. 0.75 × 262 144
   ≈ 196 600 tokens). If context passes ~90% with no compression, the
   engine is not wired — abort and check plugin enable + boot log
   (`Plugin 'lcm2-pg' registered context engine`).

## Protocol (paste to the agent under test)

```
CONTEXT-FILL + COMPRESSION TEST — follow exactly:

1. First say in one line: engine check — run terminal:
   hermes -p default config get context.engine
   and confirm it says lcm2-pg. If not, STOP and tell me.

2. Fill my context by reading real files, two reads per turn (one
   read_file of ~150+ lines of the repo plus one terminal grep/ls
   command), starting with: plugins/lcm2-pg/engine.py (pages of 200
   lines), then tools.py, then bridge/shim files, then
   docs/spec-phase0.md through spec-phase3.md, then tests/lcm2/ files
   one per turn. Keep going every turn without stopping to ask me
   anything until I interrupt you.

3. IMPORTANT — each turn, before your tool calls, state ONE anchor line,
   numbered, like:
   ANCHOR <n>: <three random made-up words> = <a random 4-digit number>
   (e.g. ANCHOR 7: purple-quasar-mongoose = 4821). Use completely
   different words each turn. These prove losslessness later — do not
   repeat yourself.

4. When I type a message like "COMPRESSION HAPPENED" (I'll be watching
   the context bar / 🗜️ counter), immediately:
   a. Tell me what you still remember verbatim from ANCHOR 1, 2 and 3
      (if you can't see them anymore, say so honestly — that is the
      expected result, not a failure).
   b. Then recall them from the store instead: use lcm_grep with a
      keyword from the oldest anchors, then lcm_expand on the matching
      store_id / node_id to recover the exact wording. List every anchor
      number it recovers vs its words+number.
   c. Run lcm_status and lcm_inspect and report: message count for this
      session, summary nodes, max DAG depth.
   d. Finish with a verdict line: LOSSLESS: YES/NO — YES only if every
      anchor you can no longer see in context was recovered verbatim
      from the store.

5. If the context bar reaches ~90%+ with no compression visible at any
   point, tell me immediately — that would mean the engine isn't
   compressing (should fire at threshold × window, see prerequisites).
```

The operator then drives turns (any nudge like "continue") until the
🗜️/context bar visibly folds, and types exactly: `COMPRESSION HAPPENED`.

## Interpreting the verdict
- **LOSSLESS: YES** — every anchor no longer visible in context was
  recovered verbatim from the store (exact words + exact 4-digit number).
  A partially-recovered or paraphrased anchor = NO.
- Seeing the *oldest* anchors vanish from context is the expected,
  correct outcome — compression working, not failing.
- Expected store state at first fold: this session's message count grows
  past hundreds, `summary_nodes` > 0 in `lcm_status`, DAG depth ≥ 1 in
  `lcm_inspect`/`lcm_status` (depth-0 leaves folded into depth-1+ nodes).
  `fresh_tail` in `lcm_inspect` shows the still-unfolded tail of the
  session.
- The anchors are planted *in the session under test* — running step 4
  in an unrelated session that never emitted anchors yields verdict
  **N/A** (nothing planted), never YES.

## Notes / gotchas
- Anchors must be random per run; reuse across runs invalidates the test.
- Recall lane is `lcm_grep` (FTS) → `lcm_expand` (exact spans / summary
  node subtree). If grep misses a distinctive anchor word, that is
  itself a finding (FTS lane broken) — check `lcm_doctor` FTS index.
- Tool-count note: lcm tools appear via the deferred catalog in some
  surfaces; `tool_search` → `tool_call` names (`lcm_grep` etc.) are the
  same engine.
- Hosts with `LCM_CONTEXT_THRESHOLD=0.75` on 262k windows: on very
  tool-heavy sessions you may see an auto-reset before a clean fold —
  re-run and compare; a reset without recoverable store rows is a real
  lossless failure, escalate it.
