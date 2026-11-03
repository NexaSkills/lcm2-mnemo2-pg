#!/usr/bin/env bash
# C2RESID leg 1 — capture-dir assertion (read-only, deterministic).
#
# Class this names (C2DSNBOUNCE-MIKE-2158 residual, manny re-emit
# 2026-11-03 22:08:28,177): context.engine=lcm2-pg set, yet every routed
# turn logs "Context engine 'lcm2-pg' not found — falling back to
# built-in compressor" (run_agent._select_context_engine). Route 1 of the
# engine selector resolves the name ONLY under $HERMES_HOME/plugins/
# (bundled dir, then user_plugins_dir()=get_hermes_home()/plugins).
# Routed turns scope get_hermes_home() per profile via a contextvar
# (gateway/run.py _profile_runtime_scope), so a profile WITHOUT
# <profile>/plugins/context_engine/lcm2-pg resolves NOTHING on route 1 —
# silently, at debug level, with no warning at all. The general-plugin
# fallback (route 2) then only surfaces a LOUD line when the plugin
# actually loaded; profiles with lcm2-pg not enabled at all (brandbuddy/
# debbie/manny/tessa shape on beez: 0 enabled) land on the silent
# "not found" line instead. That is the per-turn residual class.
#
# This script asserts, per profile, the capture-dir leg EXISTS and
# RESOLVES (readlink -f lands on the repo's context-engine dir). It never
# mutates anything: apply the leg with
#   bash scripts/onboard_profile.sh <agent>            (per profile), or
#   bash scripts/cutover_capture_dirs.sh               (all profiles, fix-only)
# then bounce the gateway (manny leg) and probe (G1 form, see fix report).
#
# Usage: capture_dir_assert.sh [agent ...]
#   no agents => every profile under $HERMES_BASE/profiles that declares
#   context.engine=lcm2-pg (via hermes config get — CLI only, never the
#   config file). Those (and only those) need the capture dir.
# Exit 0 = every lcm2-pg-routed profile has a resolving capture dir;
# 2 = any MISSING/BROKEN (names it, prints the exact apply command).
set -uo pipefail
HERMES_BASE=${HERMES_BASE:-${HERMES_REAL_HOME:-$HOME}/.hermes}
REPO=${REPO:-$(cd "$(dirname "$0")/.." && pwd)}
SRC="$REPO/plugins/context_engine/lcm2-pg"
[ -d "$SRC" ] || { echo "CAPTURE_DIR RED repo capture dir missing: $SRC" >&2; exit 2; }

TARGETS=("$@")
if [ ${#TARGETS[@]} -eq 0 ]; then
  for d in "$HERMES_BASE"/profiles/*/; do
    [ -f "$d/config.yaml" ] || continue
    p=$(basename "$d")
    eng=$(hermes -p "$p" config get context.engine 2>/dev/null | tr -d '[:space:]')
    [ "$eng" = "lcm2-pg" ] && TARGETS+=("$p")
  done
fi
FAIL=0
for p in "${TARGETS[@]}"; do
  link="$HERMES_BASE/profiles/$p/plugins/context_engine/lcm2-pg"
  tgt=$(readlink -f "$link" 2>/dev/null)
  if [ -n "$tgt" ] && [ "$tgt" = "$(readlink -f "$SRC")" ] && [ -f "$tgt/__init__.py" ]; then
    echo "CAPTURE_DIR ok   $p -> $tgt"
  elif [ -e "$link" ] || [ -L "$link" ]; then
    echo "CAPTURE_DIR RED  $p exists but wrong/dead: $link -> ${tgt:-<unresolved>}"
    echo "                 fix: ln -sfn \"$SRC\" \"$link\""
    FAIL=2
  else
    echo "CAPTURE_DIR RED  $p MISSING $link (engine=lcm2-pg, capture route resolves nothing)"
    echo "                 fix: ln -sfn \"$SRC\" \"$link\"   (or: bash scripts/onboard_profile.sh $p)"
    FAIL=2
  fi
done
if [ ${#TARGETS[@]} -eq 0 ]; then
  echo "CAPTURE_DIR note no profile declares context.engine=lcm2-pg (nothing to assert)"
fi
[ "$FAIL" = 0 ] && echo "CAPTURE_DIR GREEN $(hostname) $(date -Iseconds)"
exit "$FAIL"
