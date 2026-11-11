#!/usr/bin/env bash
# plugin_hub.sh — enforce the plugin-placement STANDARD (Jan 2026-11-11):
#   (1) every plugin RESIDES in $HERMES_HOME/plugins as a REAL directory,
#       kept in sync from the git checkout by rsync (single write-source:
#       the checkout; the hub is materialised, never hand-edited);
#   (2) every profile (except the root/default surface, which reads the hub
#       directly) gets SYMLINKS into the hub — never links straight into a
#       checkout;
#   (3) on self-contained hosts the git checkout itself lives under
#       $HERMES_HOME/checkout/<repo> (relocation leg), so nothing lives in
#       ~/ops or $HOME/ — the fleet-host customer-cleanliness rule.
# Exceptions (deliberate, do not "fix"):
#   - context_engine/lcm2-pg stays a SYMLINK into the checkout: the engine
#     is loaded by a single resolver (route 1); a second on-disk copy would
#     be a second live code path, violating the single-source invariant.
#   - nexa-kev is owned by its own repo's installer (checkout-under-
#     ~/.hermes/checkout already); this script never touches it.
# Fail-closed: a hub entry that is a symlink to a NON-checkout path (vendor
# co-existence class) or a real dir we don't manage -> FAIL, nothing renamed.
# --check: read-only verdict. Exit 0 = PLUGIN_HUB OK.
set -uo pipefail

# Source of truth for the hub: the LIVE deployment checkout, not wherever
# this script copy happens to sit. Order: relocated standard
# (~/.hermes/checkout/<repo>) -> golden instance checkout (~/<repo>) ->
# this script's own tree (dev clone last).
_NAME0=$(basename "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)")
REPO=${PLUGIN_HUB_REPO:-}
if [ -z "$REPO" ]; then
  for c in "$HOME/.hermes/checkout/$_NAME0" "$HOME/$_NAME0" \
           "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"; do
    [ -d "$c/plugins" ] && { REPO="$c"; break; }
  done
  REPO=${REPO:-$HOME/$_NAME0}
fi
# The hub lives at the SHARED ~/.hermes — deliberately NOT $HERMES_HOME,
# which is profile-scoped in a profile session (the profiles all link to
# the same shared hub). Sandbox override: PLUGIN_HUB_HOME.
HERMES_BASE=${PLUGIN_HUB_HOME:-$HOME/.hermes}
NAME=$(basename "$REPO")
DEST=${PLUGIN_HUB_DEST:-$HERMES_BASE/checkout/$NAME}
RELOCATE=${PLUGIN_HUB_RELOCATE:-0}
HUB=$HERMES_BASE/plugins
CHECK=0
[ "${1:-}" = "--check" ] && CHECK=1
# entries this script owns (repo bridge set); nexa-kev intentionally absent
NAMES="lcm2-pg mnemo-pg2 mnemosyne"
MARK=".nk-hub-sync"   # provenance marker written into each materialised dir
fails=0
OLD_REPO=${PLUGIN_HUB_OLD_REPO:-}   # pre-relocation path, for legacy-target matching
say(){ printf '%s\n' "$*"; }
ok(){ say "OK    $*"; }
fix(){ say "FIX   $*"; }
fail(){ say "FAIL  $*"; fails=$((fails+1)); }

# link_accept <linkpath> <expected-resolved-target>
# -> 0 when the link points at the expected tree. A DANGLING link is accepted
# only if its raw target sits under the (possibly just-moved) old checkout
# path — dangling-at-~/ops is the relocation artifact, dangling-at-a-vendor
# dir stays a fail-closed vendor co-existence signal.
link_accept(){
  local link=$1 want=$2 raw res
  raw=$(readlink "$link"); res=$(readlink -f "$link")
  [ "$res" = "$want" ] && return 0
  if [ -n "$raw" ] && [ ! -e "$res" ] && [ -n "$OLD_REPO" ]; then
    case "$raw" in "$OLD_REPO"/*) return 0 ;; esac
  fi
  return 1
}

# ---------- 1. relocation leg (self-contained hosts) ----------
# after a successful relocation the caller's recorded path is stale; adopt
# the standard location so re-runs don't read a missing dir as "plugin gone"
if [ ! -d "$REPO/plugins" ] && [ -d "$DEST/plugins" ]; then
  OLD_REPO="$REPO"; REPO="$DEST"
fi
if [ "$RELOCATE" = 1 ] && [ "$REPO" != "$DEST" ]; then
  if [ "$CHECK" = 1 ]; then
    [ -d "$DEST" ] && ok "checkout at $DEST" || fail "checkout NOT at $DEST (still $REPO)"
  elif [ ! -e "$DEST" ]; then
    mkdir -p "$(dirname "$DEST")"
    if mv "$REPO" "$DEST"; then
      say "FIX   checkout relocated $REPO -> $DEST"
      OLD_REPO="$REPO"; REPO="$DEST"
    else
      fail "checkout relocation refused (mv $REPO -> $DEST)"
    fi
  else
    fail "relocation target $DEST already exists (two checkouts? resolve manually, never force)"
  fi
fi

# ---------- 2. hub materialisation ----------
mkdir -p "$HUB"
for n in $NAMES; do
  src=$REPO/plugins/$n
  if [ ! -d "$src" ]; then ok "plugin $n not in this checkout (skip)"; continue; fi
  tgt=$HUB/$n
  if [ -L "$tgt" ]; then
    if ! link_accept "$tgt" "$(readlink -f "$src")"; then
      fail "hub entry $n is a symlink to NON-CHECKOUT path: $(readlink "$tgt") (vendor co-existence? refuse to touch)"
      continue
    fi
    if [ "$CHECK" = 1 ]; then fail "hub entry $n is still a legacy symlink (run without --check)"; continue; fi
    mv "$tgt" "$tgt.retired-$(date +%Y%m%d)"
    fix "hub entry $n: legacy checkout-symlink replaced by real dir"
  elif [ -e "$tgt" ] && [ ! -d "$tgt" ]; then
    fail "hub entry $n exists but is not a dir/symlink — refusing"; continue
  fi
  if [ "$CHECK" = 1 ]; then
    if [ -d "$tgt" ] && [ -f "$tgt/$MARK" ] \
       && diff -rq -x __pycache__ -x "$MARK" "$src" "$tgt" >/dev/null 2>&1; then
      ok "hub $n materialised + in sync"
    else
      [ -d "$tgt" ] || fail "hub $n missing (real dir expected)"
      [ -d "$tgt" ] && [ ! -f "$tgt/$MARK" ] && fail "hub $n real dir NOT managed by this script (no $MARK)"
      [ -d "$tgt" ] && [ -f "$tgt/$MARK" ] && fail "hub $n DRIFT vs checkout (run sync)"
    fi
    continue
  fi
  rsync -a -c --delete --exclude __pycache__ --exclude "$MARK" "$src/" "$tgt/" \
    || { fail "rsync $src -> $tgt"; continue; }
  echo "$REPO" > "$tgt/$MARK"
  ok "hub $n synced from checkout"
done

# ---------- 3. context_engine capture entry (route-1 invariant) ----------
ce_src=$REPO/plugins/context_engine/lcm2-pg
if [ -d "$ce_src" ]; then
  mkdir -p "$HUB/context_engine"
  if [ "$CHECK" = 1 ]; then
    [ "$(readlink -f "$HUB/context_engine/lcm2-pg" 2>/dev/null)" = "$(readlink -f "$ce_src")" ] \
      && ok "hub context_engine/lcm2-pg -> checkout" \
      || fail "hub context_engine/lcm2-pg missing or wrong target"
  else
    ln -sfn "$ce_src" "$HUB/context_engine/lcm2-pg" && ok "hub context_engine/lcm2-pg -> checkout"
  fi
fi

# ---------- 4. profile symlinks (hub is the only source) ----------
for pdir in "$HERMES_BASE"/profiles/*/; do
  [ -d "$pdir" ] || continue
  pname=$(basename "$pdir")
  pd=$pdir/plugins
  if [ "$CHECK" = 1 ]; then
    [ -d "$pd" ] || { ok "profile $pname: no plugins dir (nothing to link)"; continue; }
    for n in $NAMES; do
      [ -e "$pd/$n" ] || continue
      if link_accept "$pd/$n" "$(readlink -f "$HUB/$n" 2>/dev/null)" \
         && { [ "$(readlink "$pd/$n")" = "$HUB/$n" ] || link_accept "$pd/$n" "$(readlink -f "$HUB/$n" 2>/dev/null)"; }; then
        [ "$(readlink "$pd/$n")" = "$HUB/$n" ] \
          && ok "profile $pname/$n -> hub" \
          || ok "profile $pname/$n -> hub-equivalent (repoints needed)"
      else
        fail "profile $pname/$n NOT via hub (-> $(readlink "$pd/$n" 2>/dev/null))"
      fi
    done
    if [ -e "$pd/context_engine/lcm2-pg" ]; then
      if [ "$(readlink "$pd/context_engine/lcm2-pg")" = "$HUB/context_engine/lcm2-pg" ] \
         || [ "$(readlink -f "$pd/context_engine/lcm2-pg")" = "$(readlink -f "$ce_src" 2>/dev/null)" ]; then
        ok "profile $pname/context_engine/lcm2-pg -> hub"
      else
        fail "profile $pname/context_engine/lcm2-pg wrong target (-> $(readlink "$pd/$n" 2>/dev/null))"
      fi
    fi
    continue
  fi
  mkdir -p "$pd"
  for n in $NAMES; do
    [ -e "$HUB/$n" ] || continue
    if [ -L "$pd/$n" ]; then
      cur=$(readlink "$pd/$n")
      [ "$cur" = "$HUB/$n" ] && continue
      if ! link_accept "$pd/$n" "$(readlink -f "$HUB/$n")"; then
        fail "profile $pname/$n links to NON-HUB/NON-CHECKOUT path ($cur) — refuse"
        continue
      fi
      ln -sfn "$HUB/$n" "$pd/$n" && fix "profile $pname/$n repointed to hub (was $cur)"
    elif [ -e "$pd/$n" ]; then
      fail "profile $pname/$n is a REAL dir/other (expected link) — inspect manually"
    else
      ln -sfn "$HUB/$n" "$pd/$n" && fix "profile $pname/$n -> hub (created)"
    fi
  done
  if [ -d "$HUB/context_engine" ]; then
    mkdir -p "$pd/context_engine"
    if [ -L "$pd/context_engine/lcm2-pg" ] || [ ! -e "$pd/context_engine/lcm2-pg" ]; then
      cur=$(readlink "$pd/context_engine/lcm2-pg" 2>/dev/null)
      if [ "$cur" = "$HUB/context_engine/lcm2-pg" ]; then :
      elif [ -n "$cur" ] && [ "$(readlink -f "$pd/context_engine/lcm2-pg")" != "$(readlink -f "$ce_src")" ]; then
        fail "profile $pname/context_engine/lcm2-pg -> foreign ($cur) — refuse"
      else
        ln -sfn "$HUB/context_engine/lcm2-pg" "$pd/context_engine/lcm2-pg" \
          && fix "profile $pname/context_engine/lcm2-pg -> hub"
      fi
    fi
  fi
done

if [ "$fails" = 0 ]; then say "PLUGIN_HUB OK"; exit 0
else say "PLUGIN_HUB FAIL=$fails"; exit 1; fi
