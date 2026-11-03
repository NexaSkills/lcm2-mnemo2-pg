#!/usr/bin/env bash
# X-3 cure — capability tools.override DENY on the NEW stack (lcm2-pg /
# mnemo-pg2) while the deprecated legacy plugin (mnemosyne) is silently
# ALLOWED via its legacy allow_tool_override key.
#
# Root-cause (clone buddy, 296 deny lines in agent.log): the plugin loader
# calls plugin_capability_granted(plugin, "tools.override") which reads
# plugins.entries.<plugin>.granted_capabilities (canonical) OR the
# deprecated allow_tool_override key (legacy-compat). The roll kit
# (onboard_profile.sh) never grants either, so every new-stack tool
# override is denied while mnemosyne — never touched by the roll — passes
# on its legacy key. This script grants the canonical way (list key) via
# `hermes config set` ONLY (fleet rule: never file-edit config.yaml).
#
# Usage: capability_grant_fix.sh [agent ...]
#   no agents  => root config + every profile under $HERMES_BASE/profiles
# Exit 0 = every target verified (both new plugins granted); 2 = mismatch.
set -uo pipefail
HERMES_BASE=${HERMES_BASE:-${HERMES_REAL_HOME:-$HOME}/.hermes}
PLUGINS=(lcm2-pg mnemo-pg2)
CAP=tools.override
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
  for pl in "${PLUGINS[@]}"; do
    key="plugins.entries.$pl.granted_capabilities"
    cur=$(hermes ${flags[@]+"${flags[@]}"} config get "$key" 2>/dev/null | tr -d '[:space:]')
    if echo "$cur" | grep -q -- "$CAP"; then
      echo "GRANT ok  $p/$pl (already $CAP)"
      continue
    fi
    # idempotent append of the existing list (literal YAML list key, no quotes)
    list=""
    if [ -n "$cur" ] && [ "$cur" != "null" ] && [ "$cur" != "{}" ]; then
      list="$cur,"
    fi
    if ! hermes ${flags[@]+"${flags[@]}"} config set "$key" "[${list}$CAP]" >/dev/null 2>&1; then
      echo "GRANT FAIL $p/$pl — hermes config set refused"
      FAIL=1
      continue
    fi
    now=$(hermes ${flags[@]+"${flags[@]}"} config get "$key" 2>/dev/null | tr -d '[:space:]')
    if echo "$now" | grep -q -- "$CAP"; then
      echo "GRANT set  $p/$pl (+$CAP)"
    else
      echo "GRANT FAIL $p/$pl — set ran but get shows '$now'"
      FAIL=1
    fi
  done
done
[ "$FAIL" = 0 ] && echo "CAP_GRANT GREEN $(hostname) $(date -Iseconds)" || echo "CAP_GRANT RED $(hostname) $(date -Iseconds)"
exit "$FAIL"
