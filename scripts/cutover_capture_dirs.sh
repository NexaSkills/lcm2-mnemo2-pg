#!/usr/bin/env bash
# C2RESID cutover — author the capture-dir symlink for EVERY profile that
# declares context.engine=lcm2-pg (fix-only, idempotent, no config edits).
#
# Same leg as onboard_profile.sh §5 (capture-symlink), fleet-wide form:
# for each profile whose `hermes -p <p> config get context.engine` says
# lcm2-pg, ensure <profile>/plugins/context_engine/lcm2-pg -> repo
# plugins/context_engine/lcm2-pg. Config values are NEVER touched here
# (fleet rule: engine values flip only via engine_engine_align.sh /
# hermes config set). Never bounces the gateway — the apply/bounce
# sequence lives in the fix report; bounce is the operator's leg.
#
# Usage: cutover_capture_dirs.sh [--dry-run]
# Exit 0 = every lcm2-pg-routed profile now has a resolving capture dir
# (prints CAPTURE_CUTOVER GREEN + per-profile lines); 2 = any failure.
set -uo pipefail
DRY=${1:-}
HERMES_BASE=${HERMES_BASE:-${HERMES_REAL_HOME:-$HOME}/.hermes}
REPO=${REPO:-$(cd "$(dirname "$0")/.." && pwd)}
SRC="$REPO/plugins/context_engine/lcm2-pg"
[ -d "$SRC" ] || { echo "CAPTURE_CUTOVER RED repo capture dir missing: $SRC" >&2; exit 2; }
run() { if [ "$DRY" = "--dry-run" ]; then echo "DRY: $*"; else "$@"; fi; }
FAIL=0; N=0

# Route-1 target hardening (C2RESID2S): find_engine_dir's user leg is
# LAUNCH-scope (user_plugins_dir() = get_hermes_home()/plugins at boot;
# HERMES_PROFILE ignored — gate-proved C2DEPLOY-GATE). The gateway process
# resolves context.engine from the ROOT config, so the ROOT capture dir is
# a first-class target, not just the per-profile links below.
root_link="$HERMES_BASE/plugins/context_engine/lcm2-pg"
root_eng=$(hermes config get context.engine 2>/dev/null | tr -d '[:space:]')
tgt=$(readlink -f "$root_link" 2>/dev/null)
if [ "$root_eng" != "lcm2-pg" ]; then
  echo "CAPTURE_CUTOVER skip root (engine=${root_eng:-<empty>})"
elif [ -n "$tgt" ] && [ "$tgt" = "$(readlink -f "$SRC")" ]; then
  echo "CAPTURE_CUTOVER ok   root (already linked)"; N=$((N+1))
else
  run mkdir -p "$HERMES_BASE/plugins/context_engine"
  if run ln -sfn "$SRC" "$root_link"; then
    echo "CAPTURE_CUTOVER set  root -> $SRC"; N=$((N+1))
  else
    echo "CAPTURE_CUTOVER FAIL root (ln refused)" >&2; FAIL=2
  fi
fi

for d in "$HERMES_BASE"/profiles/*/; do
  [ -f "$d/config.yaml" ] || continue
  p=$(basename "$d")
  eng=$(hermes -p "$p" config get context.engine 2>/dev/null | tr -d '[:space:]')
  [ "$eng" = "lcm2-pg" ] || { echo "CAPTURE_CUTOVER skip $p (engine=${eng:-<empty>})"; continue; }
  link="$d/plugins/context_engine/lcm2-pg"
  tgt=$(readlink -f "$link" 2>/dev/null)
  if [ -n "$tgt" ] && [ "$tgt" = "$(readlink -f "$SRC")" ]; then
    echo "CAPTURE_CUTOVER ok   $p (already linked)"
    N=$((N+1)); continue
  fi
  run mkdir -p "$d/plugins/context_engine"
  if run ln -sfn "$SRC" "$link"; then
    echo "CAPTURE_CUTOVER set  $p -> $SRC"
    N=$((N+1))
  else
    echo "CAPTURE_CUTOVER FAIL $p (ln refused)" >&2; FAIL=2
  fi
done
echo "CAPTURE_CUTOVER profiles covered: $N"
[ "$DRY" = "--dry-run" ] && { echo "DRY done"; exit "$FAIL"; }
bash "$(dirname "$0")/capture_dir_assert.sh" || FAIL=2
[ "$FAIL" = 0 ] && echo "CAPTURE_CUTOVER GREEN $(hostname) $(date -Iseconds)"
exit "$FAIL"
