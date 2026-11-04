#!/usr/bin/env bash
# C2RESID3 capture-dir placement fix — one-line summary of the fix leg.
#
# ROOT CAUSE (proved this session, C2RESID2-MIKE-2310 + C2RESID2S-MIKE-0048):
#   plugins/context_engine/__init__.py::_iter_engine_dirs /
#   ::find_engine_dir consult the USER lane as
#     user_plugins_dir()/ <name>          ==  $HERMES_HOME/plugins/lcm2-pg
#   (NOT $HERMES_HOME/plugins/context_engine/<name>).  A per-profile
#   capture dir was authored at the wrong level by the earlier cutover
#   scripts, so find_engine_dir always returned None and the loader
#   emitted "Context engine 'lcm2-pg' not found - falling back to
#   built-in compressor" for every routed turn — even though the shim
#   itself imports fine, so the capture-dir assertion scripts (which
#   test the shim path directly) went GREEN while the loader lane stayed
#   dead. That is the C2DEPLOY-GATE F3 class (assertion path != loader path).
#
# FIX: move the capture dir to plugins/lcm2-pg for every surface whose
# context.engine names lcm2-pg.  Idempotent, config-blind, no bounce.
# Usage: fix_capture_placement.sh [--dry-run]
set -uo pipefail
DRY=${1:-}
HERMES_BASE=${HERMES_BASE:-${HERMES_REAL_HOME:-$HOME}/.hermes}
REPO=${REPO:-$(cd "$(dirname "$0")/.." && pwd)}
SRC="$REPO/plugins/context_engine/lcm2-pg"
[ -d "$SRC" ] || { echo "FIXPLACE RED repo capture dir missing: $SRC" >&2; exit 2; }
[ -f "$SRC/__init__.py" ] || { echo "FIXPLACE RED capture dir has no __init__.py" >&2; exit 2; }
run() { if [ "$DRY" = "--dry-run" ]; then echo "DRY: $*"; else "$@"; fi; }
FAIL=0; N=0

# returns 0 (needs link at plugins/<name>) / 1 (skip) / sets $LINK $LINKDIR $ENGINEDIR
place() {  # $1=home $2=label $3=engine
  local home=$1 label=$2 eng=$3
  if [ "$eng" != "lcm2-pg" ]; then echo "FIXPLACE skip $label (engine=${eng:-<empty>})"; return 0; fi
  LINK="$home/plugins/lcm2-pg"
  LINKDIR="$home/plugins"
  ENGINEDIR="$home/plugins/context_engine/lcm2-pg"
  local tgt cur
  cur=$(readlink -f "$LINK" 2>/dev/null)
  tgt=$(readlink -f "$SRC")
  if [ -n "$cur" ] && [ "$cur" = "$tgt" ]; then
    echo "FIXPLACE ok   $label (correctly linked)"; N=$((N+1))
    # remove a stale WRONG-level capture dir (loader never reads it; leaving
    # it invites the next person to trust the wrong path)
    [ -L "$ENGINEDIR" ] && run rm -f "$ENGINEDIR"
    return 0
  fi
  run mkdir -p "$LINKDIR"
  if ! run ln -sfn "$SRC" "$LINK"; then
    echo "FIXPLACE FAIL $label (ln refused)" >&2; FAIL=2; return 1
  fi
  echo "FIXPLACE set  $label  $LINK -> $SRC"; N=$((N+1))
  [ -L "$ENGINEDIR" ] && run rm -f "$ENGINEDIR" && echo "FIXPLACE rm-stale $label $ENGINEDIR"
  return 0
}

root_eng=$(hermes config get context.engine 2>/dev/null | tr -d '[:space:]')
place "$HERMES_BASE" root "$root_eng"
for d in "$HERMES_BASE"/profiles/*/; do
  [ -f "$d/config.yaml" ] || continue
  p=$(basename "$d")
  eng=$(hermes -p "$p" config get context.engine 2>/dev/null | tr -d '[:space:]')
  place "${d%/}" "$p" "$eng"
done

echo "FIXPLACE surfaces covered: $N"
if [ "$DRY" = "--dry-run" ]; then echo "DRY done"; exit "$FAIL"; fi
# GREEN iff every lcm2-pg-routed surface now resolves through the LOADER
# (not the shim) at the right level.
bash "$(dirname "$0")/capture_dir_assert.sh" || FAIL=2
[ "$FAIL" = 0 ] && echo "FIXPLACE GREEN $(hostname) $(date -Iseconds)"
exit "$FAIL"
