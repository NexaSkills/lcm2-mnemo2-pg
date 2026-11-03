#!/usr/bin/env bash
# C2RESID leg 2 — psycopg_pool LOUD env-depcheck (fail-closed, names the
# host file and the exact command form; changes NOTHING itself).
#
# Class this names (C2DSNBOUNCE-MIKE-2158 (3)/(5), manny re-emit
# 22:08:28,177): the capture lane imports plugins/lcm2-pg/pgconn.py whose
# FIRST lines are `require_psycopg()` + `from psycopg_pool import
# ConnectionPool`. X-4's _depcheck covers bare `psycopg` only, so in an
# environment that carries psycopg but NOT psycopg-pool (beez proof
# 2026-11-03: the 9527c3f3 gateway env venv has psycopg 3.3.6,
# psycopg_pool absent) the import dies with a plain ModuleNotFoundError
# that the capture loader swallows — silent per-turn fallback, no LOUD
# line anywhere. Same never-trust-the-surface family as X-4 itself.
#
# Two facts the operator must know (verified on beez, 2026-11-03):
#  * the 9527 venv's psycopg came in via uv (INSTALLER=uv, no
#    requirements/pyproject in the repo pins the env), so "pip install
#    psycopg-pool into the venv" works but is NOT tracked by any repo
#    file — the durable, repo-side cure is the PYTHONPATH leg below;
#  * the gateway's PYTHONPATH already carries a legacy second entry
#    (an lcm-pg-era site dir) yet the probe STILL misses the pool there
#    — a cross-version (3.13) site dir under the 3.14 gateway
#    interpreter is not reliably importable, so don't assume the second
#    PYTHONPATH entry works.
#
# Usage: env_depcheck.sh [--json]
#   probes the LIVE gateway's interpreter+import surface (/proc/<pid>/
#   environ + the interpreter named there, -I semantics like the
#   launcher's children), every profile .env's DSN sanity (delegates to
#   env_dsn_check.sh), and prints the exact fix command forms.
# Exit 0 = gateway env imports psycopg AND psycopg_pool; 2 = any leg red
# (fail-closed line names the host file to edit and the command).
set -uo pipefail
HB=${HERMES_BASE:-${HERMES_REAL_HOME:-$HOME}/.hermes}
# Resolve the gateway main pid WITHOUT systemctl (this script runs inside
# gateway turns too; the literal "systemctl ... hermes-gateway" in a
# command trips the in-gateway restart guard — fleet lint lesson). The
# pidfile is maintained by the gateway itself; /proc scan is the fallback.
PID=""
for pf in "$HB"/gateway.pid; do
  [ -r "$pf" ] || continue
  # pidfile is JSON ({"pid": N, "kind": "hermes-gateway", ...}); take the
  # "pid" field ONLY (a blind digit-strip concatenated pid+start_time).
  p=$(sed -n 's/.*"pid"[^0-9]*\([0-9][0-9]*\).*/\1/p' "$pf" | head -1)
  [ -n "$p" ] && [ -d "/proc/$p" ] && { PID=$p; break; }
done
if [ -z "$PID" ]; then
  # /proc scan last (the pidfile is authoritative when present): match
  # argv "gateway" + "run" (the systemd ExecStart pair) whose process
  # env HERMES_HOME equals $HB — the pidfile lives under $HB, so a
  # different home is a DIFFERENT gateway (test/fake home must never
  # borrow a live host's pid; fleet lesson from the same green->red bug).
  self=$$
  hb_norm=${HB%/}
  for cand in /proc/[0-9]*/cmdline; do
    [ "$(dirname "$cand")" = "/proc/$self" ] && continue
    tr '\0' '\n' < "$cand" 2>/dev/null | grep -qx gateway || continue
    tr '\0' '\n' < "$cand" 2>/dev/null | grep -qx run || continue
    pid=${cand#/proc/}; pid=${pid%/cmdline}
    h=$(tr '\0' '\n' < "/proc/$pid/environ" 2>/dev/null | sed -n 's/^HERMES_HOME=//p' | head -1)
    [ "${h%/}" = "$hb_norm" ] || continue
    PID=$pid; break
  done
fi
[ -n "${PID:-}" ] && [ -d "/proc/$PID" ] || { echo "ENVDEPCHECK RED gateway pid not found (pidfile $HB/gateway.pid or /proc scan)" >&2; exit 2; }
MAP=/proc/$PID/environ
[ -r "$MAP" ] || { echo "ENVDEPCHECK RED cannot read $MAP" >&2; exit 2; }
EXE=$(tr '\0' '\n' < "$MAP" | sed -n 's/^_\?ARGV0=//p' | head -1)
[ -x "${EXE:-}" ] || EXE=$(readlink -f /proc/$PID/exe 2>/dev/null)
[ -x "${EXE:-}" ] || { echo "ENVDEPCHECK RED cannot resolve gateway interpreter exe (pid $PID)" >&2; exit 2; }
PP=$(tr '\0' '\n' < "$MAP" | sed -n 's/^PYTHONPATH=//p' | head -1)

IMPORT_PROBE='import importlib.util as u,sys
missing=[m for m in ("psycopg","psycopg_pool") if u.find_spec(m) is None]
print("OK" if not missing else "MISSING:"+",".join(missing)); sys.exit(0 if not missing else 3)'
out=$(env -u PYTHONPATH PYTHONPATH="$PP" "$EXE" -I -c "$IMPORT_PROBE" 2>&1); rc=$?
FAIL=0
if [ "$rc" = 0 ] && [ "$out" = OK ]; then
  echo "ENVDEPCHECK ok   gateway env (pid $PID) imports psycopg + psycopg_pool (PYTHONPATH entries: $(printf '%s' "$PP" | tr ':' '\n' | grep -c . ))"
else
  echo "ENVDEPCHECK RED  gateway env (pid $PID, exe $EXE) CANNOT import: ${out:-probe failed rc=$rc}" >&2
  echo "ENVDEPCHECK FIX  capture-lane import (plugins/lcm2-pg/pgconn.py 'from psycopg_pool import ConnectionPool') dies SILENTLY per-turn in this env. Two durable command forms (operator leg; this script changes nothing):" >&2
  echo "  (a) install into the env the gateway already carries first:" >&2
  echo "      $EXE -m pip install psycopg-pool" >&2
  echo "      NOTE: 9527-style installs are uv-made and a plain pip add is UNTRACKED by any repo file — pair it with (b) or record it." >&2
  echo "  (b) PYTHONPATH leg via a systemd drop-in (host file:" >&2
  echo "      ~/.config/systemd/user/hermes-gateway.service.d/95-lcm2-pool.conf):" >&2
  printf '      [Service]\nEnvironment=PYTHONPATH=%s:%s\n' "${PP%%:*}" "<ABSOLUTE-PATH-OF-A-python3.14-COMPATIBLE-site-packages-HOLDING-psycopg_pool>" >&2
  echo "      apply+bounce are the OPERATOR's leg (done from a shell OUTSIDE the gateway process, not from here)" >&2
  echo "      (find a source env: grep -l psycopg_pool ~/.hermes/installs/*/environments/*/venv/lib/python3.*/site-packages -d skip)" >&2
  FAIL=2
fi

# DSN sanity across root + profile .env (same fail-closed family)
if [ -x "$(command -v bash)" ] && [ -f "$(dirname "$0")/env_dsn_check.sh" ]; then
  if bash "$(dirname "$0")/env_dsn_check.sh" >/dev/null 2>&1; then
    echo "ENVDEPCHECK ok   env_dsn_check (root + profile .env DSNs parse)"
  else
    echo "ENVDEPCHECK RED  env_dsn_check found merged/unparseable DSN lines (run scripts/env_dsn_check.sh to see file:line)" >&2
    FAIL=2
  fi
fi

[ "$FAIL" = 0 ] && echo "ENVDEPCHECK GREEN $(hostname) $(date -Iseconds)"
exit "$FAIL"
