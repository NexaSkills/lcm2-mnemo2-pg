#!/usr/bin/env bash
# LCM2-FLEET-SPRINT-1 X-1 — engine_slot_prove.sh: deterministic proof of
# WHICH context engine holds the host slot, WITHOUT a boot.
#
# Runs the repo's engine_slot resolver against a throwaway HERMES_HOME
# containing each claimed plugin layout, and asserts the outcome the
# X-1 rule promises:
#   both stacks + CONTEXT_ENGINE=new  -> lcm2-pg wins   (marker)
#   both stacks + CONTEXT_ENGINE=old  -> lcm-pg wins    (marker)
#   lcm2-pg only                      -> single-stack   (no rule)
#   lcm-pg  only                      -> single-stack   (no rule)
# Plus the live-shape checks from engine_slot_assert.sh stay separate:
# this script proves the RULE is deterministic, not that a boot applied
# it (that leg is the boot-log + live-DB assertions in check_all).
#
# Exit 0 = all green. Any RED line names the case. Token X1.
set -uo pipefail
REPO="${REPO:-$HOME/lcm2-mnemo2-pg}"
ES="$REPO/plugins/lcm2-pg/engine_slot.py"
[ -f "$ES" ] || { echo "X1PROVE RED engine_slot.py not found at $ES"; exit 2; }
WORK="$(mktemp -d "${TMPDIR:-/tmp}/x1prove.XXXXXX")"
trap 'rm -rf "$WORK"' EXIT
FAIL=0

prove () { # name, expect_winner, layout(lcm-pg|lcm2-pg|both), env-assignments...
  local name="$1" expect="$2" layout="$3"; shift 3
  local home="$WORK/home_$name"
  mkdir -p "$home/plugins"
  case "$layout" in
    both) mkdir -p "$home/plugins/lcm-pg" "$home/plugins/lcm2-pg";;
    lcm-pg) mkdir -p "$home/plugins/lcm-pg";;
    lcm2-pg) mkdir -p "$home/plugins/lcm2-pg";;
  esac
  local out
  # rival-installed is decided by the REAL _rival_installed() against
  # $home/plugins (fixture dirs): single-layout cases must genuinely
  # short-circuit; the both-layout cases must see the rival dirs.
  out=$(env -u CONTEXT_ENGINE -u LCM2_PG_DSN -u LCM2_PG_HOST -u LCM2_PG_PORT \
        HOME_ISOLATION=1 HERMES_HOME="$home" "$@" \
        python3 -c "
import importlib.util,sys
spec=importlib.util.spec_from_file_location('es','$ES')
m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
print('|'.join(m.resolve_engine_slot('lcm2-pg')))
" 2>&1) || { echo "X1PROVE RED $name resolver crashed: $out"; FAIL=1; return; }
  local winner="${out%%|*}"
  if [ "$winner" = "$expect" ]; then
    echo "X1PROVE ok   $name winner=$winner ($out)"
  else
    echo "X1PROVE RED  $name winner=$winner expected=$expect ($out)"
    FAIL=1
  fi
}

# resolve_engine_slot returns the WINNER SIDE ("new"/"old"/""), not a
# plugin name — expectations are sides.
prove marker-new   new       both    CONTEXT_ENGINE=new
prove marker-old   old       both    CONTEXT_ENGINE=old
prove single-new           ""        lcm2-pg
prove single-old           ""        lcm-pg
prove dsn-new      new       both    LCM2_PG_DSN="dbname=x host=/x port=1 user=u"
# Live-shape leg (no live-instance dependency, works in clean env):
# probe-live opens a listener on an ephemeral port and points the probe
# at it => new; probe-dead points it at a closed port => old (default:legacy).
probe_live () { # name, expect, port
  local name="$1" expect="$2" port="$3"
  local home="$WORK/home_$name"
  mkdir -p "$home/plugins/lcm-pg" "$home/plugins/lcm2-pg"
  local out
  out=$(env -u CONTEXT_ENGINE -u LCM2_PG_DSN \
        HOME_ISOLATION=1 HERMES_HOME="$home" \
        LCM2_PG_HOST=127.0.0.1 LCM2_PG_PORT="$port" \
        LISTEN_PORT="$port" python3 -c "
import socket,importlib.util
s=socket.socket(); s.bind(('127.0.0.1',int(__import__('os').environ['LISTEN_PORT']))); s.listen(8)
spec=importlib.util.spec_from_file_location('es','$ES')
m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
print('|'.join(m.resolve_engine_slot('lcm2-pg')))
" 2>&1) || { echo "X1PROVE RED $name crashed: $out"; FAIL=1; return; }
  local winner="${out%%|*}"
  if [ "$winner" = "$expect" ]; then
    echo "X1PROVE ok   $name winner=$winner ($out)"
  else
    echo "X1PROVE RED  $name winner=$winner expected=$expect ($out)"
    FAIL=1
  fi
}
LP=$(python3 -c "import socket;s=socket.socket();s.bind(('127.0.0.1',0));print(s.getsockname()[1])")
probe_live probe-live new "$LP"
probe_live probe-dead old 1

if [ "$FAIL" = 0 ]; then
  echo "X1PROVE GREEN $(hostname) $(date -Iseconds) — engine-slot rule deterministic across layouts"
  exit 0
fi
echo "X1PROVE RED $(hostname) $(date -Iseconds) — see lines above"
exit 2
