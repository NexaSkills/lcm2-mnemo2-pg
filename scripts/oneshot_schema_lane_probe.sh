#!/usr/bin/env bash
# oneshot_schema_lane_probe.sh — W3BEEZGAP-MIKE-1610 GAP3: operator probe
# for the one-shot CLI (`hermes -z`) context-engine schema lane.
#
# WHY THIS IS A PROBE AND NOT A FIX (repo ticket W3BEEZGAP-GAP3, mechanism
# cited below): the context engine derives its schema from the live
# HERMES_HOME (plugins/lcm2-pg/pgconn.py agent_schema, L51-73) and the
# context-engine capture dir PINS that derivation at register() time
# (plugins/context_engine/lcm2-pg/__init__.py _ensure_schema L102-134,
# env LCM2_AGENT_SCHEMA). In a long-lived daemon the pin is harmless
# because the home is re-scoped per routed turn via contextvar (B2 fix).
# In a ONE-SHOT -z process everything binds once — so whichever home the
# vendor CLI settled on decides the lane. Vendor resolution
# (hermes_cli/main.py _apply_profile_override, L605-614):
#
#   hermes_home_env = os.environ.get("HERMES_HOME", "")
#   if profile_name is None and hermes_home_env and Path(hermes_home_env).parent.name == "profiles":
#       return                      # early-return: exported home wins
#   ...
#   hermes_home = resolve_profile_env(profile_name)   # -p handled AFTER
#   os.environ["HERMES_HOME"] = hermes_home
#
# The early-return only fires when -p is ABSENT (guarded
# `profile_name is None`), so -p itself is resolved. The GAP CLASS the
# QA gate measured (context rows landing in lcm2__hermes) is the
# NO-HERMES_HOME root-launch shape: with neither -p nor an exported home,
# the home = ~/.hermes root -> basename "_hermes" -> lcm2__hermes, while
# mnemo-pg2's runtime warn leg (W3TKT2 #5, 9866f78) already names that
# ghost-bank class on the MEMORY side. The context-engine side had no
# equivalent assertion — this probe + the parity note below close it.
#
# WHAT IT DOES: for up to --max host-served profiles, run one tiny -z turn
# under a chosen launcher env (--home-set sets HERMES_HOME for the run;
# omit = clean root-launch) and count where the token rows landed:
# own schema (correct) vs lcm2__hermes (collapse) vs neither (no ingest).
# Read-only against the DB except the -z turn itself; leaves probe tokens
# in messages (operator sweeps with scripts/residual_sweep.sh).
#
# Usage: oneshot_schema_lane_probe.sh [--token STR] [--max N] [--home-set DIR]
# Exit 0 = every probed profile's rows landed ONLY in its own schema; 1 = offenders.
set -uo pipefail
TOKEN=PROBE; MAX=3; HB_HOME=""
HB=${HERMES_BASE:-${HERMES_REAL_HOME:-$HOME}/.hermes}
DSN=${NEW_DSN:-"host=$HOME/lcm2-mnemo2-pg/pg/run port=54331 dbname=lcm2db user=mnemo201"}
PSQL=${PSQL_BIN:-psql}
while [ $# -gt 0 ]; do case "$1" in
  --token) TOKEN=$2; shift 2;; --max) MAX=$2; shift 2;; --home-set) HB_HOME=$2; shift 2;;
  *) echo "usage: $0 [--token STR] [--max N] [--home-set DIR]" >&2; exit 2;; esac; done
nq() { $PSQL "$DSN" -v ON_ERROR_STOP=1 -Atc "$1"; }
fail=0; shown=0
for d in "$HB"/profiles/*/; do
  p=$(basename "$d")
  case "$p" in .*) continue;; esac
  [ -f "$d/config.yaml" ] || continue   # host-served profiles only (identity marker)
  [ "$shown" -ge "$MAX" ] && break
  shown=$((shown+1))
  tok="LANEPROBE-${TOKEN}-${p}-$(date +%s)"
  if [ -n "$HB_HOME" ]; then
    HERMES_HOME="$HB_HOME" hermes -p "$p" --yolo -z "Reply with exactly: $tok" >/dev/null 2>&1 || { echo "WARN: -z failed for $p"; continue; }
  else
    env -u HERMES_HOME hermes -p "$p" --yolo -z "Reply with exactly: $tok" >/dev/null 2>&1 || { echo "WARN: -z failed for $p"; continue; }
  fi
  own=$(nq "select count(*) from lcm2_${p}.messages where content like '%${tok}%'" 2>/dev/null || echo ERR)
  her=$(nq "select count(*) from lcm2__hermes.messages where content like '%${tok}%'" 2>/dev/null || echo 0)
  if [ "$own" != "0" ] && [ "$own" != "ERR" ] && [ "$her" = "0" ]; then
    echo "PASS $p -> lcm2_${p} ($own rows)"
  else
    echo "FAIL $p own=${own} lcm2__hermes=${her} (lane collapse or no ingest)"
    fail=1
  fi
done
[ $fail = 0 ] && echo "LANE_PROBE PASS" || echo "LANE_PROBE FAIL"
exit $fail
