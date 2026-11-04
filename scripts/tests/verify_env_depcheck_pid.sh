#!/usr/bin/env bash
# W3TKT-MIKE-1220 ticket #4 — env_depcheck.sh gateway-pid resolution harness.
#
# Defect cured: resolution assumed HERMES_BASE == the real hermes home
# ($HB/gateway.pid + a /proc scan comparing HERMES_HOME to $HB). A gateway
# whose actual HERMES_HOME differs from $HB was never found ("gateway pid
# not found" RED). Cure: discover the live `gateway run` process first,
# read the pidfile from ITS OWN home via /proc/<pid>/environ HERMES_HOME,
# require the pidfile record to equal the live pid (never borrow another
# gateway's pid), keep $HB/gateway.pid as the documented fallback.
#
# Mechanism: this harness runs env_depcheck.sh with PROC_DIR pointed at a
# fake /proc tree (host-agnostic; production default is /proc).
#  FIXTURE A (regression core): gateway lives in a home != HERMES_BASE;
#    pidfile under the gateway's own home; HERMES_BASE=/nonexistent so the
#    $HB fallback path cannot exist. Expect: pid DISCOVERED (the script
#    proceeds past pid resolution — its later probe legs may be RED, that
#    is fine; the harness asserts ONLY that it did not die with
#    "gateway pid not found").
#  FIXTURE B (no-borrow): the home pidfile records a FOREIGN (dead) pid
#    while the live process has a different pid. Expect: that process is
#    NOT adopted via the env-home leg; with HERMES_BASE=/nonexistent the
#    script dies RED (never borrows).
#  FIXTURE C (fallback intact): no matching gateway process; a valid
#    pidfile under HERMES_BASE itself. Expect: fallback resolves (again
#    asserted by NOT dying "gateway pid not found").
set -u
REPO="${REPO:-$(cd "$(dirname "$0")/../.." && pwd)}"
DEP="$REPO/scripts/env_depcheck.sh"
[ -f "$DEP" ] || { echo "HARNESS: $DEP missing"; exit 1; }
FAIL=0

alive() { mkdir -p "$1/proc/$2"; }
proc_entry() {  # proc_entry <fx> <pid> <argv0..> (NUL-separated cmdline)
  local fx=$1 pid=$2; shift 2
  mkdir -p "$fx/proc/$pid"
  { for a in "$@"; do printf '%s\0' "$a"; done; } > "$fx/proc/$pid/cmdline"
}
proc_env() {  # proc_env <fx> <pid> KEY=VALUE...
  local fx=$1 pid=$2; shift 2
  mkdir -p "$fx/proc/$pid"
  { for a in "$@"; do printf '%s\0' "$a"; done; } > "$fx/proc/$pid/environ"
}

run_dep() {  # run_dep <fx-proc-dir> <hermes-base> -> full stdout+stderr
  PROC_DIR="$1" HERMES_BASE="$2" bash "$DEP" 2>&1
}

# --- FIXTURE A: gateway home != HERMES_BASE (the regression core) ----------
FXA=$(mktemp -d /tmp/edcA.XXXXXX)
mkdir -p "$FXA/home_real"
# decoy: unrelated live process
proc_entry "$FXA" 99900 /usr/bin/bash -c "sleep 999"
proc_env   "$FXA" 99900 HERMES_HOME=/somewhere/else
alive "$FXA" 99900
# the fake gateway: systemd launcher form (gateway run inside the -c payload)
proc_entry "$FXA" 99901 /usr/bin/fake-python -I -c "launcher stub for /x/hermes-agent" gateway run
proc_env   "$FXA" 99901 HERMES_HOME="$FXA/home_real"
alive "$FXA" 99901
printf '{"pid": 99901, "kind": "hermes-gateway", "start_time": 1730000000}\n' > "$FXA/home_real/gateway.pid"
out=$(run_dep "$FXA/proc" /nonexistent-base-w3tkt)
case "$out" in
  *"gateway pid not found"*) echo "FAIL fixture A: died 'gateway pid not found' — env-home discovery leg missing (pre-fix behaviour)"; echo "$out" | head -3; FAIL=1;;
  *) echo "PASS fixture A: gateway in a foreign home discovered (HB=/nonexistent)";;
esac

# --- FIXTURE B: home pidfile records a foreign (dead) pid — never borrowed -
FXB=$(mktemp -d /tmp/edcB.XXXXXX)
mkdir -p "$FXB/home_real"
proc_entry "$FXB" 99901 /usr/bin/fake-python -I -c "gateway run stub"
proc_env   "$FXB" 99901 HERMES_HOME="$FXB/home_real"
alive "$FXB" 99901
printf '{"pid": 88888, "kind": "hermes-gateway"}\n' > "$FXB/home_real/gateway.pid"
# 88888 deliberately absent from the fake proc (dead pid)
out=$(run_dep "$FXB/proc" /nonexistent-base-w3tkt)
case "$out" in
  *"gateway pid not found"*) echo "PASS fixture B: foreign pid in the env-home pidfile NOT adopted (script dies RED, no borrow)";;
  *"pid 88888"*) echo "FAIL fixture B: adopted the foreign pid 88888 from the env-home pidfile"; FAIL=1;;
  *) echo "FAIL fixture B: unclear outcome"; echo "$out" | head -3; FAIL=1;;
esac

# --- FIXTURE C: no gateway process at all; $HB/gateway.pid fallback --------
FXC=$(mktemp -d /tmp/edcC.XXXXXX)
mkdir -p "$FXC/proc" "$FXC/base"
printf '{"pid": 99902, "kind": "hermes-gateway"}\n' > "$FXC/base/gateway.pid"
alive "$FXC" 99902
out=$(run_dep "$FXC/proc" "$FXC/base")
case "$out" in
  *"gateway pid not found"*) echo "FAIL fixture C: documented \$HB/gateway.pid fallback lost"; echo "$out" | head -3; FAIL=1;;
  *) echo "PASS fixture C: \$HB/gateway.pid fallback still resolves";;
esac

rm -rf "$FXA" "$FXB" "$FXC"
[ "$FAIL" = 0 ] && echo "ENVDEPCHECK_PID_HARNESS PASS" || echo "ENVDEPCHECK_PID_HARNESS FAIL"
exit "$FAIL"
