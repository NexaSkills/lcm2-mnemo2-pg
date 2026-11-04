#!/usr/bin/env bash
# g1loader_verify.sh — G1 PREDICTOR (C2RESID2-MIKE-2310; R1/R4).
#
# Asserts route 1 of the host's context-engine loader yields a real
# engine INSTANCE for every surface that declares context.engine=<name>:
#   find_engine_dir('<name>') -> dir  AND  load_context_engine('<name>') -> non-None
# A NO-INSTANCE here means a routed turn of that surface emits
# "Context engine '<name>' not found — falling back to built-in
# compressor" — agent_init._select_context_engine makes the SAME calls.
#
# Why this form (tessa C2DEPLOY-GATE F2, token C2DEPLOY-GATE-TESSA-2236):
# the deploy passed every existing check (env_depcheck GREEN, route-2
# visibility GREEN, routed turns silent) while the engine lane was DEAD,
# because every prior probe re-injected PYTHONPATH into a child whose
# launcher pops it. This probe runs the loader inside the gateway
# interpreter (the tools exe from the live pidfile's argv) with the env
# the launcher's child actually has (PYTHONHOME/PYTHONPATH/VIRTUAL_ENV
# popped — the probe does the popping itself, faithfully) and takes the
# verdict from the loader CALL, never an import-health proxy.
# Route-1 resolution is LAUNCH-scope (user_plugins_dir() =
# get_hermes_home()/plugins; HERMES_PROFILE is IGNORED — gate-proved):
# the root surface is the one a gateway-routed turn sees; each profile
# declaring the engine is additionally probed as its own launch
# (standalone-CLI surface).
# Read-only: imports only (the engine's own idempotent bootstrap may
# run — what a routed turn does anyway). Zero config/gateway writes.
#
# Usage: g1loader_verify.sh [engine-name]     (default lcm2-pg)
# Exit 0 + "G1LOADER INSTANCE" iff EVERY probed surface yields one;
# 2 otherwise (names the dead surface + the fallback emit it implies).
set -uo pipefail
NAME=${1:-lcm2-pg}
case "$NAME" in (*[!A-Za-z0-9._-]*|'') echo "G1LOADER RED bad engine name: $NAME" >&2; exit 2;; esac
HB=${HERMES_BASE:-${HERMES_REAL_HOME:-$HOME}/.hermes}
ROOT=${HERMES_ROOT:-$HB/hermes-agent}
REPO=${LCM2_REPO:-$HOME/lcm2-mnemo2-pg}
PROBE_SRC="$REPO/scripts/g1loader_probe.py"
[ -f "$PROBE_SRC" ] || PROBE_SRC="$(cd "$(dirname "$0")" && pwd)/g1loader_probe.py"
[ -f "$PROBE_SRC" ] || { echo "G1LOADER RED probe missing (looked in $REPO/scripts and beside this script)" >&2; exit 2; }

# Gateway interpreter: the live gateway's own exe (pidfile argv[0]),
# else the launcher's runtime command, else python3.
EXE=""
GWPID=$(sed -n 's/.*"pid"[^0-9]*\([0-9][0-9]*\).*/\1/p' "$HB/gateway.pid" 2>/dev/null | head -1)
if [ -n "${GWPID:-}" ] && [ -r "/proc/$GWPID/cmdline" ]; then
  readarray -t -d '' ARGV < "/proc/$GWPID/cmdline" 2>/dev/null || true
  EXE=${ARGV[0]:-}
fi
if [ -z "${EXE:-}" ] || [ ! -x "$EXE" ]; then
  LAUNCHER=$(command -v hermes || true)
  [ -x "${LAUNCHER:-}" ] || LAUNCHER="$ROOT/.hermes/bin/hermes"
  RJ=$(env -i HOME="$HOME" HERMES_HOME="$HB" "$LAUNCHER" --print-runtime-command gateway run 2>/dev/null | tail -1)
  case "${RJ:-}" in \[*)
    EXE=$(python3 -c 'import json,sys; print(json.loads(sys.stdin.read())[0])' <<<"$RJ" 2>/dev/null) ;;
  esac
fi
[ -n "${EXE:-}" ] && [ -x "$EXE" ] || EXE=$(command -v python3)
[ -x "${EXE:-}" ] || { echo "G1LOADER RED no interpreter" >&2; exit 2; }

# hermes_constants' config reader shells out to the `hermes` CLI; on a
# bare launcher child PATH it is missing => engine reads as unset. The
# probe would still answer from the repo default, but a real routed
# turn gets the config value — shim PATH so both see the CLI.
SHIM=$(mktemp -d "${TMPDIR:-/tmp}/g1shim.XXXXXX")
trap 'rm -rf "$SHIM"' EXIT
for h in "$(command -v hermes || true)" "$ROOT/.hermes/bin/hermes" "$HOME/.local/bin/hermes"; do
  if [ -n "$h" ] && [ -x "$h" ]; then ln -sf "$h" "$SHIM/hermes"; break; fi
done

FAIL=0; N=0
echo "G1LOADER form: exe=$EXE (gateway exe)"
probe_surface(){  # <label> <hermes-home>
  local label="$1" hh="$2" out rc
  out=$(PATH="$SHIM:$PATH" HERMES_HOME="$hh" "$EXE" -I "$PROBE_SRC" "$NAME" 2>&1); rc=$?
  local lines
  lines=$(printf '%s\n' "$out" | grep -E "^(G1SURFACE|route1_plugins_dirs|find_engine_dir|load_context_engine|G1LOADER|probe RAISED)")
  if [ -z "$lines" ]; then
    echo "$label G1LOADER NO-INSTANCE (probe produced no verdict line; rc=$rc)"
    printf '%s\n' "$out" | tail -3 | sed "s|^|$label   |"
    return 2
  fi
  printf '%s\n' "$lines" | sed "s|^|$label |"
  if [ -n "${VERBOSE:-}" ]; then
    printf '%s\n' "$out" | grep -iE "WARNING:(plugins|lcm2)|not found|Failed to" | sed "s|^|$label   |" | head -6
  fi
  printf '%s\n' "$lines" | grep -q "^G1LOADER NO-INSTANCE" && return 2
  return "$rc"
}

probe_surface "root   " "$HB" || FAIL=2
N=$((N+1))
for d in "$HB"/profiles/*/; do
  [ -f "$d/config.yaml" ] || continue
  p=$(basename "$d")
  eng=$(hermes -p "$p" config get context.engine 2>/dev/null | tr -d '[:space:]')
  [ "$eng" = "$NAME" ] || { echo "G1LOADER skip $p (engine=${eng:-<empty>})"; continue; }
  probe_surface "profile $p" "${d%/}" || FAIL=2
  N=$((N+1))
done

if [ "$FAIL" = 0 ] && [ "$N" -gt 0 ]; then
  echo "G1LOADER INSTANCE $(hostname) surfaces=$N engine=$NAME $(date -Iseconds)"
else
  echo "G1LOADER NO-INSTANCE $(hostname) engine=$NAME — route 1 yields no INSTANCE for at least one surface declaring context.engine=$NAME (line above names it). Routed turns there emit \"Context engine '$NAME' not found — falling back to built-in compressor\". Fix the capture route (R1) BEFORE relying on routed-turn probes." >&2
fi
exit "$FAIL"
