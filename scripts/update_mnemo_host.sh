#!/usr/bin/env bash
# update_mnemo_host.sh — canonical per-host update for the mnemo-pg2
# (lcm2-mnemo2-pg repo) deployment, mirroring the lcm2-pg rollout lane:
#   pull -> instance watchdog -> dbs -> surfaces (incl. fastembed +
#   embeddings migration) -> check. Services are NEVER bounced here;
#   the script prints the bounce command when one is warranted.
# Flags: --no-pull (dev), --skip-check.
set -uo pipefail
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
NO_PULL=0; SKIP_CHECK=0
for a in "$@"; do case $a in
  --no-pull) NO_PULL=1;; --skip-check) SKIP_CHECK=1;; --relocate) :;;
  *) echo "usage: update_mnemo_host.sh [--no-pull] [--skip-check] [--relocate]"; exit 2;;
esac; done
# --relocate: opt into the checkout relocation leg even without the
# LCM2_SELF_CONTAINED=1 marker in root .env (fleet bootstrap one-off)
for a in "$@"; do [ "$a" = "--relocate" ] && export PLUGIN_HUB_RELOCATE=1; done

step() { printf '\n== %s ==\n' "$*"; }
rc_total=0

if [ "$NO_PULL" = 0 ]; then
  step "pull ($REPO_DIR)"
  git -C "$REPO_DIR" pull --ff-only || { echo "FAIL  pull (diverged? resolve manually, never force)"; exit 1; }
  git -C "$REPO_DIR" log --oneline -1
fi

step "instance watchdog"
bash "$REPO_DIR/scripts/ensure_instance.sh" || rc_total=1

# plugin-placement STANDARD (Jan 2026-11-11): real-dir hub at
# ~/.hermes/plugins + profile symlinks into it; on self-contained hosts
# (LCM2_SELF_CONTAINED=1 in root .env) the checkout itself moves under
# ~/.hermes/checkout. Set PLUGIN_HUB_RELOCATE=0 here to pin the leg off.
step "plugin hub"
SC_ENV="$HOME/.hermes/.env"
SC=0
grep -q "^LCM2_SELF_CONTAINED=1" "$SC_ENV" 2>/dev/null && SC=1
PHOUT=$(PLUGIN_HUB_RELOCATE="${PLUGIN_HUB_RELOCATE:-$SC}" \
        bash "$REPO_DIR/scripts/plugin_hub.sh" 2>&1) || rc_total=1
echo "$PHOUT"
# if the relocation leg MOVED this very checkout, adopt the new path —
# every later step resolves its scripts through REPO_DIR
case "$PHOUT" in *"checkout relocated"*)
  NEW="$HOME/.hermes/checkout/$(basename "$REPO_DIR")"
  [ -d "$NEW/scripts" ] && { REPO_DIR="$NEW"; echo "NOTE  REPO_DIR -> $REPO_DIR"; } ;;
esac
# persistence: without the marker, a later run from the golden ~/ path
# would flip the layout back — stamp it once relocation is in effect
if [ "$(basename "$(dirname "$REPO_DIR")")" = ".hermes" ] && \
   ! grep -q "^LCM2_SELF_CONTAINED=1" "$SC_ENV" 2>/dev/null; then
  echo "LCM2_SELF_CONTAINED=1" >> "$SC_ENV" && echo "NOTE  LCM2_SELF_CONTAINED=1 stamped in $SC_ENV"
fi

step "dbs/profiles"
bash "$REPO_DIR/scripts/setup_dbs.sh" || rc_total=1

step "surfaces (drop-ins, params, legacy disable, fastembed, embed_migrate)"
bash "$REPO_DIR/scripts/apply_surface_fixes.sh" || rc_total=1

if [ "$SKIP_CHECK" = 0 ]; then
  step "read-only check"
  bash "$REPO_DIR/scripts/check_mnemo.sh" || rc_total=1
fi

step "result"
if [ "$rc_total" = 0 ]; then
  echo "PASS  host updated. If this host runs a hermes gateway, one bounce"
  echo "      is now warranted (only Jan runs service commands):"
  systemctl --user list-unit-files 2>/dev/null | awk '/^hermes-gateway/ {print "        systemctl --user restart "$1}' | head -4
else
  echo "FAIL  host update finished with problems; see lines above"
fi
exit "$rc_total"
