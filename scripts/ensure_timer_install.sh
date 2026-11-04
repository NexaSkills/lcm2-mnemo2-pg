#!/usr/bin/env bash
# X-2 cure — lcm2-mnemo2-ensure.timer was hand-authored on warren/trade only
# (active there) but NEVER installed by the roll on template/clone (X-2:
# ensure.timer inactive on template/clone). The watchdog runs
# scripts/ensure_instance.sh (self-heals role/hba/dbs; never touches
# :54329). This script installs the pair IDEMPOTENTLY and enables the timer
# — host-agnostic ($HOME-based, repo self-located; fleet rule: no literals).
#
# Usage: ensure_timer_install.sh   (root/user-level, no profiles)
# Exit 0 = timer loaded+enabled+active; 1 = any leg failed (names it).
set -uo pipefail
REPO=${REPO:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}
UNITDIR="$HOME/.config/systemd/user"
ENSURE="$REPO/scripts/ensure_instance.sh"
[ -x "$ENSURE" ] || { echo "TIMER FAIL — $ENSURE missing/not executable"; exit 1; }
mkdir -p "$UNITDIR"

install_unit() {  # $1=name  $2=content : replace only when content differs
  local f="$UNITDIR/$1"
  if [ -f "$f" ] && [ "$(cat "$f")" = "$2" ]; then
    echo "TIMER ok   $1 (already installed)"; return 0
  fi
  [ -f "$f" ] && cp -a "$f" "$f.bak-$(date +%Y%m%d-%H%M%S)"
  printf '%s' "$2" > "$f"
  echo "TIMER set  $1"
}

install_unit lcm2-mnemo2-ensure.service "[Unit]
Description=LCM2/MNEMO2 instance watchdog (self-heal role/hba/dbs; never touches :54329)

[Service]
Type=oneshot
ExecStart=$ENSURE"

install_unit lcm2-mnemo2-ensure.timer "[Unit]
Description=LCM2/MNEMO2 instance watchdog timer (2 min)

[Timer]
OnBootSec=1min
OnUnitActiveSec=2min
AccuracySec=10s

[Install]
WantedBy=timers.target"

systemctl --user daemon-reload || { echo "TIMER FAIL — daemon-reload"; exit 1; }
systemctl --user enable --now lcm2-mnemo2-ensure.timer || {
  echo "TIMER FAIL — enable --now"; exit 1; }

# verify leg (never trust the surface)
# X-2 SUITEGREEN-MIKE-0810: `enable --now` on an OnBootSec= timer starts it,
# but a daemon-reload with unit-file content changes in the same window
# REPLACES the unit object and the pending start is lost — timer stays
# enabled, surfaces inactive until its first elapse. Live incident (beez,
# 2026-11-04 07:09:18 journal: "Stopped ... watchdog timer", unit files
# rewritten, "Reloading finished" — service kept firing every 2min yet
# is-active=inactive persisted for 77min, until an explicit start at
# 08:28:02). Nudge the start (idempotent; a running timer is a no-op),
# and accept a timer that demonstrably FIRES even when the surface reads
# idle (the old `enabled AND active` hardcode called a WORKING watchdog
# RED: 'TIMER RED (enabled= active=inactive)'). GREEN = enabled AND
# (surface-active OR journal shows starts in the last 15min — past
# OnUnitActiveSec=2min + slack, so a genuinely stalled timer shows stale).
st=$(systemctl --user is-enabled lcm2-mnemo2-ensure.timer 2>/dev/null)
act=$(systemctl --user is-active lcm2-mnemo2-ensure.timer 2>/dev/null)
if [ "$st" = enabled ] && [ "$act" != active ]; then
  systemctl --user start lcm2-mnemo2-ensure.timer >/dev/null 2>&1
  act=$(systemctl --user is-active lcm2-mnemo2-ensure.timer 2>/dev/null)
fi
if [ "$st" = enabled ] && [ "$act" != active ]; then
  # not surface-active right now: prove it FIRES via the service's journal
  firings=$(journalctl --user -u lcm2-mnemo2-ensure.service --since "-15 min" \
            --no-pager -q 2>/dev/null \
            | grep -icE "Start(ed|ing) LCM2/MNEMO2 instance watchdog")
else
  firings=0
fi
firing=0
[ "$act" = active ] && firing=1
[ "${firings:-0}" -gt 0 ] && firing=1
if [ "$st" = enabled ] && [ "$firing" = 1 ]; then
  echo "TIMER_GREEN $(hostname) $(date -Iseconds) (enabled+firing: timer=$act recent_starts=$firings)"
  exit 0
fi
echo "TIMER RED $(hostname) (enabled=$st active=$act recent_starts=${firings:-?})"
exit 1
