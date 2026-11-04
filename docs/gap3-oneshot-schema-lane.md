# GAP3 (W3BEEZGAP-MIKE-1610): one-shot CLI `-z` context lane vs profile identity

## Measured (beez 2026-11-04, QA gate W3BEEZFULL-TESSA-1515 C-B + this leg)
- Daemon/gateway lanes: own `lcm2_<p>` always (contextvar scoping, B2 fix).
- Memory side: `mnemo2_<p>` always, in every lane (funnel + W3TKT2 warn leg).
- One-shot `-z` from a DAEMON-scoped shell (HERMES_HOME exported,
  points at `profiles/<launcher>`): non-default profiles bind OWN schema
  (mike 2/2, debbie 2/2, brandbuddy 2/2, buttercup 2/2 at gate;
  reproduced live: `hermes -p buttercup --yolo -z` from mike's shell ->
  3/3 rows `lcm2_buttercup`, 0 in `lcm2__hermes`).
- `hermes -p default -z` from that same shell: OWN schema
  (`lcm2_default`, 3/3 rows, probe W3BEEZGAP-PROBE-A leg resolved).
- `hermes -z` (root-launch, HERMES_HOME UNSET — the QA's instrumented
  leg): rows land in `lcm2__hermes` (basename rule on `~/.hermes`).

## Mechanism (vendor files cited, never edited here)
- `plugins/lcm2-pg/pgconn.py` `agent_schema()` (L51-73): schema =
  `lcm2_<basename(live HERMES_HOME)>` through
  `hermes_constants.get_hermes_home()` (contextvar override ->
  HERMES_HOME -> platform default). A one-shot process binds ONCE —
  whatever home the CLI settled is the schema for the whole run.
- `plugins/context_engine/lcm2-pg/__init__.py` `_ensure_schema`
  (L102-134): pins `LCM2_AGENT_SCHEMA` at register() from that same
  derivation (honors an existing pin; leaves no residue).
- vendor `hermes_cli/main.py` `_apply_profile_override` (L605-614 +
  L632-648): an EXPORTED `HERMES_HOME` pointing at `profiles/<x>` wins by
  early-return **only when `-p` is absent** (the guard is
  `profile_name is None`); with `-p` present, `resolve_profile_env()`
  overwrites `HERMES_HOME`. No `-p`, no export -> home = `~/.hermes`
  root -> basename `_hermes` -> `lcm2__hermes`.

## The gap class + parity note
The root-launch collapse writes context rows into the root home's schema
while MEMORY writes for the same run correctly carry the requested
profile's bank. The memory side of exactly this class got a runtime
fail-closed warning in W3TKT2 #5 (`9866f78`, ghost-derivation warn in
mnemo-pg2 `agent_schema`). The context-engine side had no equivalent —
this repo's cure is the operator-side probe
`scripts/oneshot_schema_lane_probe.sh` (model-free mechanism already
proven; probe verifies per host with real -z turns + DB counts).

## Status
Host fix belongs to vendor (`main.py`): making an explicit `-p` always
win over an inherited `HERMES_HOME` export is vendor policy (it would
break the systemd/`#22502` trust rule the early-return exists for); a
repo-side `LCM2_AGENT_SCHEMA` pre-pin from the `-p` name would forge a
second identity source that contradicts the B2 funnel contract
(`tests/lcm2/test_b2_leak_contextvar_home.py`), so it is deliberately
NOT done. Named-ticket close with probe + citations above.
