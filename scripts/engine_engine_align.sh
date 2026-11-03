#!/usr/bin/env bash
# X-10 cure — compression-counters-static fleet-wide: align context.engine
# with the NEW stack on a rolled host.
#
# Mechanism this fixes (root-cause, MIKE-X10-0432): after the lcm2 roll the
# legacy lcm-pg plugin still wins the host's ONE context-engine slot because
# context.engine is still "lcm" (root config on template/clone; the
# profiles' own values are "lcm" too). With the old :54329 instance torn
# down, the slot's engine fails every ingest ("couldn't get a connection
# after 10.00 sec") and lcm2_<p>.messages/summary_nodes never advance —
# counters STATIC fleet-wide (debbie rebattery-close.md PARTIAL driver).
#
# The roll kit itself set context.engine=lcm (onboard_profile.sh §4) — this
# script is the superseding flip leg: set it to lcm2-pg so the host's
# config-driven engine loader (agent_init._select_context_engine) resolves
# the repo's context-engine capture dir (plugins/context_engine/lcm2-pg —
# the capture lane: bridge subclass, passes the isinstance gate) instead of
# the legacy slot. Config via `hermes -p <p> config set` ONLY (fleet rule:
# never file-edit config.yaml).
#
# Usage: engine_engine_align.sh [agent ...]
#   no agents  => root config + every profile under $HERMES_BASE/profiles
# Exit 0 = every target verified "lcm2-pg"; 2 = any mismatch (names it).
set -uo pipefail
HERMES_BASE=${HERMES_BASE:-${HERMES_REAL_HOME:-$HOME}/.hermes}
WANT=lcm2-pg
TARGETS=("$@")
if [ ${#TARGETS[@]} -eq 0 ]; then
  TARGETS=(root)
  for d in "$HERMES_BASE"/profiles/*/; do
    [ -f "$d/config.yaml" ] || continue
    TARGETS+=("$(basename "$d")")
  done
fi
FAIL=0
for p in "${TARGETS[@]}"; do
  if [ "$p" = root ]; then flags=(); else flags=(-p "$p"); fi
  cur=$(hermes ${flags[@]+"${flags[@]}"} config get context.engine 2>/dev/null | tr -d '[:space:]')
  if [ "$cur" = "$WANT" ]; then
    echo "ALIGN ok  $p (already $WANT)"
    continue
  fi
  if ! hermes ${flags[@]+"${flags[@]}"} config set context.engine "$WANT" >/dev/null 2>&1; then
    echo "ALIGN FAIL $p — hermes config set refused (was $cur)"; FAIL=1; continue
  fi
  now=$(hermes ${flags[@]+"${flags[@]}"} config get context.engine 2>/dev/null | tr -d '[:space:]')
  if [ "$now" = "$WANT" ]; then
    echo "ALIGN set  $p ($cur -> $WANT)"
  else
    echo "ALIGN FAIL $p — set ran but get shows '$now' (expected $WANT)"; FAIL=1
  fi
done
[ "$FAIL" = 0 ] && echo "ENGINE_ALIGN GREEN $(hostname) $(date -Iseconds)" || echo "ENGINE_ALIGN RED $(hostname) $(date -Iseconds)"
exit "$FAIL"
