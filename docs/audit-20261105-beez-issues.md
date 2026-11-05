# lcm2-pg / mnemo2-pg health audit — mybuddy-beez — 2026-11-05

Auditor: mike. Scope: this host only. Method: live system inspection
(systemd, journal, sockets, banks), fork-vs-vendor code comparison, and
real-functionality tests through the deployed surfaces (hermes CLI chat lane,
mnemosyne_* tool calls). No test scripts standing outside the solution.

## VERIFIED HEALTHY (real functionality, live)

| Check | Result |
|---|---|
| Gateway CLI chat lane (`hermes -p mike chat -q`, the surface that broke on trade/warren) | PASS — clean turn, 0 "falling back to built-in compressor" today, 0 "no engine instance" in journal |
| LCM2 ingest | PASS — probe session 20261105_094200_17f30a landed as lcm2_mike.messages store_id 13708-13709; 1467 rows today across 155 sessions |
| Mnemo2 write+recall via real mnemosyne_remember/recall tools | PASS — write-> mnemo2_mike.working_memory, recall ranked it #1 (vec_sim 0.62); probe deleted after |
| Deployment wiring | PASS — profile plugin dirs are symlinks into ~/lcm2-mnemo2-pg working tree @ tip 1b923d7 (W3TRADESCHEMA fix live); root + profile lcm-pg disabled everywhere |
| Preflight doom-loop fix (MANNY-AUDIT-1940) | PRESENT at tip (engine.py:365 -> should_compress(None)); the 02:56 "Preflight compression ~155k >= 0 threshold" spam predates the 08:11 gateway restart — expected pre-fix residue |
| Primary :54331 cluster | healthy; ensure watchdog logs steady "OK instance healthy" |

## FINDING 1 — BROKEN: lcm2-mnemo2-fix8.service crash-loop (HIGH, low blast radius)
- systemd restart counter >1000, a FATAL every ~2.3s, since ~Nov 3 (boot
  timestamp in the stale pid file; log rotation means journal is the evidence).
- Root cause: ~/lcm2-mnemo2-pg-fix8/pg/data/postmaster.pid claims PID 685.
  PID 685 today is pipewire-pulse (PID wrap after boot) and is NOT a postgres
  backend (verified via /proc/685/exe and ps). No postgres serves that data
  dir; no socket on :54333. Stale lock -> deterministic 2s crash-loop.
- No .env anywhere references :54333/54332/54335 except scripts/split_env.sh
  (an operator opt-in). The fix8 checkout is a detached Nov-2 bisection tree
  (branches fix8/fix11), long superseded by main.
- Vendor comparison: vendor has NO systemd unit model at all; the units are
  ours (self-contained cluster design). A permanently-failing enabled unit is
  NOT expected behaviour in any design — it's abandoned-test residue.
- Fix: stop + disable the unit; archive ~/lcm2-mnemo2-pg-fix8 (and the
  superseded bisect trees). No data risk: nothing connects to it.
- Test: journal FATALs stop; `systemctl --user is-enabled` = disabled;
  primary lane unaffected.

## FINDING 2 — EXPECTED (cosmetic): "there is already a transaction in
progress" WARNINGs on legacy :54329
- Source: vendor lcm-pg code's `BEGIN IMMEDIATE` (assertion_store.py:317,
  command.py x6). SQLite dialect; harmless no-op there; on PG every call
  emits the warning when already in-txn. This is inherited vendor behaviour,
  not a fork regression. ~63 warnings in one burst at 09:11-09:12, 0 in the
  last hour.
- Driver: the legacy lcm-pg plugin is STILL WRITING to :54329 (lcm_manny
  ~16k rows vs lcm2_manny 0). On beez it is disabled root+profile everywhere —
  correct per the W3WARF1 cutover design; residual writes trace to pre-restart
  lanes. No beez-side code change warranted.
- Test: none needed beyond confirming no new burst after next boot.

## FINDING 3 — RESIDUE (housekeeping, MEDIUM): dead-instance clusters
- lcm2-mnemo2-split (:54332) and lcm2-mnemo2-qa6 (:54335) are ENABLED at boot,
  running (3 clusters x ~230MB), but referenced by ZERO profile .envs and zero
  gateway configs. Deliberate QA/fix-era instances per scripts/split_env.sh.
- Fix: disable + stop (keep data dirs until Jan confirms); frees ~460MB.

## FINDING 4 — RESIDUE (LOW): ghost banks + ghost profile dirs
- lcm2__hermes: 351 rows, last write 2026-11-05 00:14 (pre-fix; the
  W3TRADESCHEMA-MIKE-0030 fix at tip stops new writes). Leave as legacy.
- lcm2_alice/hb/mybuddy/mnemo2agent schemas: empty, no live lanes.
- ~/.hermes/profiles/f1r4/ and skilltest/: NOT registered profiles ("does not
  exist" from hermes) yet each holds a resurrection lcm.db (Nov 4 02:00) that
  the sqlite tripwire never flagged. Investigate creator (gate-test run?) and
  purge dirs.
- lcm_default on :54329 keeps 15206 FTS rows after last write Nov 3 16:02
  (vacuum churn; legacy lane, retires with the lane).

## PLAN (ordered, each step ends with a live test)
1. `systemctl --user disable --now lcm2-mnemo2-fix8.service` (F1)
   -> test: 5 min clean journal + a real CLI chat turn still ingests.
2. disable --now split + qa6 (F3), keep data dirs -> test: primary lane chat
   turn + memory recall still pass; RAM freed.
3. Purge ghost profile dirs after identifying origin (F4b); add dir-name check
   to the sqlite tripwire if origin turns out recurring.
4. (Optional, only if Jan wants the :54329 warning noise gone — it is vendor
   code, so the cure is a PG-safe BEGIN wrapper in OUR plugin copy, not
   vendor edits.)

## LIVE TEST LOG (all against the real deployed solution)
- 09:42 CLI chat -q mike lane: reply OK, 2 rows into lcm2_mike.messages.
- 09:42 mnemosyne_remember -> row in mnemo2_mike.working_memory; recall hit
  #1; forget OK.
- 4 postgres clusters running on beez; only :54329 + :54331 carry live lanes.
