#!/usr/bin/env bash
# cutover_profile.sh — W2 the ONE procedure a profile goes through to move
# from the legacy lcm/mnemosyne stack to lcm2-pg/mnemo2-pg (lcm2-seq-cutover
# W2; Jan's D-2/D-4: beez runs it EXACTLY as a fleet host will).
#
# For ONE profile, in order:
#   (1) quarantine old lcm/mnemosyne plugin dirs for that profile
#       (rename to .retired.<ts> — NEVER delete; Jan D-2),
#   (2) run scripts/onboard_profile.sh (post-1a: engine=lcm2-pg form; creates
#       lcm2_<p>+mnemo2_<p> and writes both DSNs),
#   (3) fail-closed asserts: capture_dir_assert.sh + env_depcheck.sh (scoped
#       to this profile's .env) + engine_slot_assert.sh,
#   (4) R-MAIN-ONLY hygiene: unset any auxiliary.*.model override that is set
#       in THIS profile's own config (CLI unset only; the "auto" you still see
#       afterwards is the built-in DEFAULT, not a set value — D-3 targets
#       actual overrides).
#
# NOT part of this script (deliberately):
#   - the gateway bounce (manny's step, outside the worker session),
#   - G1/G3 (post-bounce agent.log + compression-delta legs),
#   - anything touching another profile (default hermes CLI resolves to the
#     root config — never call it without -p here).
#
# Usage: cutover_profile.sh <profile> [--dry-run] [--no-test]
#   --no-test skips the pytest -x -q green leg (only legit for a dry rehearse;
#   an EXECUTED cutover on a pattern host ships WITH the test leg).
# Exit 0 = cutover done for that profile (bounce still pending -> G1 later).
set -uo pipefail

PROFILE=${1:?usage: cutover_profile.sh <profile> [--dry-run] [--no-test]}
shift
DRY=""; NOTEST=0
for a in "$@"; do
  case "$a" in
    --dry-run) DRY=--dry-run ;;
    --no-test) NOTEST=1 ;;
    *) echo "FATAL: unknown arg $a" >&2; exit 2 ;;
  esac
done
REPO=${REPO:-$(cd "$(dirname "$0")/.." && pwd)}
HERMES_BASE=${HERMES_BASE:-${HERMES_REAL_HOME:-$HOME}/.hermes}
PDIR="$HERMES_BASE/profiles/$PROFILE"
TS=$(date +%Y%m%d%H%M%S)
fail() { echo "CUTOVER RED ($PROFILE): $*" >&2; exit 1; }
[ -f "$PDIR/config.yaml" ] || fail "no profile at $PDIR (config.yaml missing)"

echo "== CUTOVER $PROFILE (ts=$TS) =="

# ---------------------------------------------------------------- 1. quarantine
echo "-- 1. quarantine old lcm/mnemosyne plugin dirs (Jan D-2: rename, never delete)"
QUARANTINED=()
for d in "$PDIR/plugins"/*; do
  [ -e "$d" ] || [ -L "$d" ] || continue
  base=$(basename "$d")
  # legacy plugin dirs/links only: lcm-pg (the old context engine) and the old
  # sqlite mnemosyne stack. mnemo-pg2/mnemosyne->repo/mnemo2* are the NEW stack
  # (onboard re-authors them); anything else is unrelated and untouched.
  case "$base" in
    lcm-pg|lcm-pg-*|mnemosyne-sqlite|mnemosyne.retired*|lcm) ;;
    *) continue ;;
  esac
  [ "$base" = "mnemosyne" ] && {
    # a symlink resolving INTO this repo is the NEW discovery bridge, not old
    r=$(readlink -f "$d" 2>/dev/null)
    case "$r" in "$REPO"/*) echo "   keep   $base (new-stack bridge -> $r)"; continue ;; esac
  }
  if [ -n "$DRY" ]; then echo "DRY: mv $d ${d}.retired.$TS"; QUARANTINED+=("$base"); continue; fi
  mv "$d" "${d}.retired.$TS" && { echo "   retire $base -> ${base}.retired.$TS"; QUARANTINED+=("$base"); } \
    || fail "cannot quarantine $d"
done
[ ${#QUARANTINED[@]} -eq 0 ] && echo "   (nothing legacy to quarantine)"

# ---------------------------------------------------------------- 2. onboard
echo "-- 2. onboard_profile.sh (post-1a: engine=lcm2-pg form)"
if [ -n "$DRY" ]; then echo "DRY: bash $REPO/scripts/onboard_profile.sh $PROFILE --dry-run"; else
  bash "$REPO/scripts/onboard_profile.sh" "$PROFILE" || fail "onboard_profile.sh failed"
fi
# W2FIX-MIKE-0524 F-1b: fail-closed audit that onboard NEVER wrote the
# legacy engine for this profile. config.yaml is read via CLI only (fleet
# rule: never file-edit); the raw grep is a read-only belt check that the
# on-disk value is exactly `lcm2-pg` and the legacy `lcm` was never the
# written value. engine_engine_align.sh below stays as the idempotent
# convergence leg/assert, NOT the fix (onboard §4 now writes lcm2-pg).
eng_pre=$(hermes -p "$PROFILE" config get context.engine 2>/dev/null | tr -d '[:space:]')
grep -Eq '^[[:space:]]*engine:[[:space:]]*"?lcm"?[[:space:]]*$' "$PDIR/config.yaml" 2>/dev/null \
  && echo "WARN: config.yaml still carries legacy engine=lcm (pre-$PROFILE-onboard residue or legacy writer)" >&2
[ -z "$DRY" ] && [ "$eng_pre" = "lcm" ] && fail "onboard wrote legacy context.engine=lcm (W1-1a fix must be in onboard_profile.sh §4; align leg may not paper over it)"
[ -z "$DRY" ] && bash "$REPO/scripts/engine_engine_align.sh" "$PROFILE" || true
# engine must READ lcm2-pg via CLI (assert, never file-edit):
eng=$(hermes -p "$PROFILE" config get context.engine 2>/dev/null | tr -d '[:space:]')
[ -z "$DRY" ] && [ "$eng" != "lcm2-pg" ] && fail "config context.engine=$eng even after engine_engine_align.sh"
echo "   context.engine=$eng"

# ---------------------------------------------------------------- 3. asserts
echo "-- 3. fail-closed asserts"
if [ -z "$DRY" ]; then
  bash "$REPO/scripts/capture_dir_assert.sh" "$PROFILE" || fail "capture_dir_assert (see lines above)"
  # env_depcheck takes NO args: it probes the live gateway env + every profile
  # .env DSN (incl. this one) host-wide; fail-closed as briefed.
  bash "$REPO/scripts/env_depcheck.sh" || fail "env_depcheck (see lines above; names the host file + fix command)"
  # W3TKT2-MIKE-1235 #5: fail-closed host-wide audit that every SERVED profile
  # carries its own MNEMO2_PG_DSN (an absent-config routed fall-through would
  # otherwise bank silently to mnemo2_default; the routing lever itself is
  # vendor Hermes code — this audit is the repo-side guard, CLOSE row cites).
  bash "$REPO/scripts/mnemo2_profile_dsn_audit.sh" || fail "mnemo2_profile_dsn_audit (served profile lacks MNEMO2_PG_DSN; names the .env to fix)"
  echo "   asserts GREEN for $PROFILE"
fi

# ---------------------------------------------------------------- 4. model hygiene
echo "-- 4. R-MAIN-ONLY: unset per-profile auxiliary.*.model overrides (CLI only)"
if [ -z "$DRY" ]; then
  found_override=0
  while read -r slot val; do
    [ -n "$slot" ] || continue
    found_override=1
    echo "   unset auxiliary.$slot.model (was: $val)"
    hermes -p "$PROFILE" config unset "auxiliary.$slot.model" 2>/dev/null \
      || fail "config unset auxiliary.$slot.model"
  done < <(hermes -p "$PROFILE" config get auxiliary 2>/dev/null | awk '
    /^[a-z_]+:/    { slot=$1; sub(/:$/,"",slot) }
    /^  model:/    { v=$2; gsub(/'"'"'/,"",v); if (v != "" && v != "auto") print slot, v }
  ')
  [ "$found_override" = 0 ] && echo "   no real auxiliary.*.model override set for $PROFILE ('auto' values are the built-in defaults, not set values)"
fi

# ---------------------------------------------------------------- 5. test leg
if [ "$NOTEST" = 1 ]; then
  echo "-- 5. test leg SKIPPED (--no-test)"
elif [ -n "$DRY" ]; then
  echo "DRY: (cd $REPO && pytest -x -q)"
else
  echo "-- 5. pytest -x -q (repo suite)"
  ( cd "$REPO" && pytest -x -q ) || fail "pytest -x -q not green"
fi

# ---------------------------------------------------------------- 6. summary
echo "-- 6. summary ($PROFILE)"
echo "quarantined: ${QUARANTINED[*]:-none} (suffix .retired.$TS)"
echo "pre-bounce gateway state (manny's bounce precondition, record before/after):"
systemctl --user show hermes-gateway -p MainPID -p ActiveEnterTimestamp -p ActiveState 2>/dev/null | sed 's/^/   /' || true
echo "NEXT (manny, outside this session): bounce the gateway, then G1 agent.log + G3 counters + tessa gate."
[ -n "$DRY" ] && echo "DRY done" || echo "CUTOVER GREEN $PROFILE $(hostname) $(date -Iseconds)"
exit 0
