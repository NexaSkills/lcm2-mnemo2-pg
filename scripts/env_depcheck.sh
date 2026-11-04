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
# W3TKT-MIKE-1220 #4: single /proc root for every probe below (production
# default /proc; targeted tests point it at a fake /proc tree — the ONLY
# test hook this script gains, the resolution logic itself is unchanged).
PROC_ROOT=${PROC_DIR:-/proc}
# Resolve the gateway main pid WITHOUT systemctl (this script runs inside
# gateway turns too; the literal "systemctl ... hermes-gateway" in a
# command trips the in-gateway restart guard — fleet lint lesson). The
# pidfile is maintained by the gateway itself; /proc scan is the fallback.
PID=""
# W3TKT-MIKE-1220 #4: the running gateway knows its OWN home — derive the
# pidfile from /proc/<pid>/environ HERMES_HOME of the live gateway process
# FIRST (a gateway started with a HERMES_HOME that differs from $HB — env
# override, a root launch with a custom home — keeps its pidfile under ITS
# own home, and the $HB path below would miss it entirely). The former
# primary leg ($HB/gateway.pid) stays as the documented fallback, and the
# /proc scan last. Same pid-record parsing on either path.
_read_pidfile() {  # _read_pidfile <path> -> pid on stdout ("" = none/absent)
  local pf=$1 p
  [ -r "$pf" ] || return 0
  # pidfile is JSON ({"pid": N, "kind": "hermes-gateway", ...}); take the
  # "pid" field ONLY (a blind digit-strip concatenated pid+start_time).
  p=$(sed -n 's/.*"pid"[^0-9]*\([0-9][0-9]*\).*/\1/p' "$pf" | head -1)
  [ -n "$p" ] && [ -d "$PROC_ROOT/$p" ] && printf '%s' "$p"
}
for cand in "$PROC_ROOT"/[0-9]*/cmdline; do
  [ "$cand" = "$PROC_ROOT/$$/cmdline" ] && continue
  [ -r "$cand" ] || continue
  # W3TKT-MIKE-1220 #4: match the LAUNCHER form too — the systemd
  # ExecStart runs `python -I -c '<launcher>' gateway run`, so "gateway"
  # and "run" can sit inside ONE -c payload rather than as separate argv
  # words (the old word-only scan saw nothing on beez while the $HB
  # pidfile leg carried it; with HERMES_BASE != real home even that leg
  # is gone and the script died RED "gateway pid not found").
  cl=$(tr '\0' '\n' < "$cand" 2>/dev/null) || continue
  printf '%s\n' "$cl" | grep -q gateway || continue
  printf '%s\n' "$cl" | grep -qx run || printf '%s\n' "$cl" | grep -Eq 'gateway run|-c .*\bgateway\b.*\brun\b' || continue
  pid=${cand#"$PROC_ROOT"/}; pid=${pid%/cmdline}
  h=$(tr '\0' '\n' < "$PROC_ROOT/$pid/environ" 2>/dev/null | sed -n 's/^HERMES_HOME=//p' | head -1)
  [ -n "$h" ] || continue
  p=$(_read_pidfile "${h%/}/gateway.pid")
  [ -n "$p" ] || continue
  # The running process must carry its home's OWN pidfile record —
  # a pid that belongs to a DIFFERENT gateway is never borrowed
  # (fleet lesson: a test/fake home must never borrow a live host's pid).
  [ "$p" = "$pid" ] || continue
  PID=$p; break
done
for pf in "$HB"/gateway.pid; do
  p=$(_read_pidfile "$pf")
  [ -n "$p" ] && { PID=$p; break; }
done
if [ -z "$PID" ]; then
  # /proc scan last (the pidfile is authoritative when present): match
  # argv "gateway" + "run" (the systemd ExecStart pair) whose process
  # env HERMES_HOME equals $HB — the pidfile lives under $HB, so a
  # different home is a DIFFERENT gateway (test/fake home must never
  # borrow a live host's pid; fleet lesson from the same green->red bug).
  self=$$
  hb_norm=${HB%/}
  for cand in "$PROC_ROOT"/[0-9]*/cmdline; do
    [ "$cand" = "$PROC_ROOT/$self/cmdline" ] && continue
    [ -r "$cand" ] || continue
    tr '\0' '\n' < "$cand" 2>/dev/null | grep -qx gateway || continue
    tr '\0' '\n' < "$cand" 2>/dev/null | grep -qx run || continue
    pid=${cand#"$PROC_ROOT"/}; pid=${pid%/cmdline}
    h=$(tr '\0' '\n' < "$PROC_ROOT/$pid/environ" 2>/dev/null | sed -n 's/^HERMES_HOME=//p' | head -1)
    [ "${h%/}" = "$hb_norm" ] || continue
    PID=$pid; break
  done
fi
[ -n "${PID:-}" ] && [ -d "$PROC_ROOT/$PID" ] || { echo "ENVDEPCHECK RED gateway pid not found (pidfile $HB/gateway.pid or /proc scan)" >&2; exit 2; }
MAP=$PROC_ROOT/$PID/environ
[ -r "$MAP" ] || { echo "ENVDEPCHECK RED cannot read $MAP" >&2; exit 2; }
EXE=$(tr '\0' '\n' < "$MAP" | sed -n 's/^_\?ARGV0=//p' | head -1)
[ -x "${EXE:-}" ] || EXE=$(readlink -f "$PROC_ROOT/$PID/exe" 2>/dev/null)
[ -x "${EXE:-}" ] || { echo "ENVDEPCHECK RED cannot resolve gateway interpreter exe (pid $PID)" >&2; exit 2; }
PP=$(tr '\0' '\n' < "$MAP" | sed -n 's/^PYTHONPATH=//p' | head -1)

IMPORT_PROBE='import importlib.util as u,sys
missing=[m for m in ("psycopg","psycopg_pool","ruamel","yaml") if u.find_spec(m) is None]
print("OK" if not missing else "MISSING:"+",".join(missing)); sys.exit(0 if not missing else 3)'
out=$(env -u PYTHONPATH PYTHONPATH="$PP" "$EXE" -I -c "$IMPORT_PROBE" 2>&1); rc=$?
# Default-start sibling probe (C2RESID2S): a hermes-CLI SUBPROCESS of the
# gateway runs WITHOUT this PYTHONPATH (the CLI launcher itself runs
# `exe -I` — site sets sys.path from the exe's own lib dirs + user site,
# and -I disables the user site). The in-process import chain below
# (hermes_cli.config -> hermes_yaml -> ruamel) must import on THAT
# surface too when anything shells out to the CLI, so probe it the same
# fail-closed way (guarded find_spec: dotted misses RAISE there).
DSN_PROBE='import importlib.util as u,sys
def miss(m):
    try: return u.find_spec(m) is None
    except (ImportError, ValueError): return True
missing=[m for m in ("ruamel.yaml","yaml") if miss(m)]
print("OK" if not missing else "MISSING:"+",".join(missing)); sys.exit(0 if not missing else 3)'
dsnout=$(env -u PYTHONPATH -u PYTHONHOME -u VIRTUAL_ENV "$EXE" -I -c "$DSN_PROBE" 2>&1); dsnpc=$?
FAIL=0
if [ "$rc" = 0 ] && [ "$out" = OK ] && [ "$dsnpc" = 0 ] && [ "$dsnout" = OK ]; then
  echo "ENVDEPCHECK ok   gateway env (pid $PID) imports psycopg + psycopg_pool (+ ruamel/yaml config stack on BOTH the PYTHONPATH env and the default-start CLI subprocess env)"
elif [ "$rc" = 0 ] && [ "$out" = OK ] && { [ "$dsnpc" != 0 ] || [ "$dsnout" != OK ]; }; then
  # the PYTHONPATH env is fine but the CLI-subprocess surface is not —
  # the distinct C2RESID2S class: g1loader emits "Failed to load context
  # engine: No module named 'ruamel'" while psycopg legs stay green.
  echo "ENVDEPCHECK RED  gateway exe $EXE default-start subprocess (NO PYTHONPATH — how the hermes CLI launcher actually runs: exe -I, user-site disabled) CANNOT import the config stack: ${dsnout:-probe failed rc=$dsnpc}" >&2
  echo "ENVDEPCHECK FIX  hermes_cli.config -> hermes_yaml -> ruamel.yaml is an IN-PROCESS import chain on this exe; psycopg installs alone never put ruamel/pyyaml on the exe's own default sys.path. Install into a dir the exe carries by DEFAULT (its lib/python3.*/site-packages) — operator leg, this script changes nothing:" >&2
  echo "      $EXE -m pip install --target <exe-lib>/lib/python3.*/site-packages ruamel.yaml pyyaml   (or a --target dir added via a PYTHONPATH drop-in ONLY if it also reaches the -I subprocess; -I ignores env PYTHONPATH and ~/.local both)" >&2
  echo "      apply+bounce are the OPERATOR's leg (done from a shell OUTSIDE the gateway process, not from here)" >&2
  FAIL=2
else
  case "$out" in
    *ruamel*|*yaml*)
      # C2RESID2S: name BOTH surfaces precisely. The gateway process
      # itself carries systemd-drop-in PYTHONPATH entries; the hermes CLI
      # launcher and its children run `exe -I` (env PYTHONPATH and the
      # user site are IGNORED under -I). ruamel/yaml must import on both.
      echo "ENVDEPCHECK RED  gateway env (pid $PID, exe $EXE) CANNOT import: ${out:-probe failed rc=$rc}  [PYTHONPATH env; default-start -I subprocess: ${dsnout:-?}]" >&2
      echo "ENVDEPCHECK FIX  hermes_cli.config imports hermes_yaml which imports ruamel.yaml at MODULE scope. The context-engine capture lane calls user_plugin_host() -> _plugins_config() -> load_config_readonly() inside the gateway interpreter: if ruamel is missing THERE, the whole capture load dies (\"Failed to load context engine\" -> silent per-turn fallback), regardless of capture links. Two surfaces to fix (operator legs; this script changes nothing):" >&2
      echo "  (a) the PYTHONPATH env the gateway runs with (first entry above, or the launcher env venv ~/.hermes/installs/*/environments/*/venv):" >&2
      echo "      <that-venv-bin>/python -m pip install ruamel.yaml pyyaml" >&2
      echo "  (b) the exe's own default-start surface — the hermes CLI launcher runs '$EXE -I ...': -I IGNORES PYTHONPATH AND the user site (~/.local), so a --user or drop-in fix is INVISIBLE there. ruamel+yaml must live inside the exe's built-in site-packages:" >&2
      echo "      $EXE -m pip install --target <dir-on-its-default-sys.path, i.e. <exe-root>/lib/python3.*/site-packages> ruamel.yaml pyyaml" >&2
      echo "  (then bounce from OUTSIDE the gateway process)" >&2
      FAIL=2 ;;
    *)
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
  FAIL=2 ;;
  esac
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
