#!/usr/bin/env bash
# apply_surface_fixes.sh — idempotent post-clone surface fixes proven on beez
# 2026-11-05. Run AFTER setup_dbs.sh, BEFORE bouncing the gateway, as the user
# who owns the systemd --user units. Host-agnostic (Omarchy fleet).
#
# Covers exactly three classes (all verified live on beez):
#   1. BOOT ORDER: gateway/webui/dashboard must start AFTER the lcm2-mnemo2-pg
#      cluster (:54331) — without this, a cold boot races the engine's DB
#      connect and the lane silently falls back to the built-in compressor.
#   2. THRESHOLD PARAMS (repo-owned params, Jan 2026-11-05):
#      - lcm2-pg engine trigger  -> LCM_CONTEXT_THRESHOLD=0.75 in root .env
#        and every profile .env (engine reads env first; defacto mechanism).
#      - Hermes built-in compressor -> compression.threshold 0.95 per profile
#        via `hermes config set` ONLY (never hand-edit config.yaml). The
#        built-in is safeguard-only: it exists solely when no engine loaded.
#   3. LEGACY MACHINERY DISABLED: legacy lcm-pg (and mnemo-pg v1 where
#      present) disabled per profile + root; stale probe cluster units
#      (fix8/qa6/split) disabled so they cannot crash-loop at boot.
set -euo pipefail

HERMES=${HERMES:-hermes}
U="$HOME/.config/systemd/user"
THRESH_ENGINE=${THRESH_ENGINE:-0.75}   # lcm2-pg trigger (fraction of window)
THRESH_SAFEGUARD=${THRESH_SAFEGUARD:-0.95}  # built-in compressor fallback
SURFACES="hermes-gateway hermes-webui hermes-dashboard"
# host-agnostic: also cover lane-variant units actually present (e.g.
# hermes-gateway-default.service on template-style hosts)
for u in $(systemctl --user list-unit-files -t service --no-legend 2>/dev/null | awk '{print $1}' | grep -E '^hermes-(gateway|webui|dashboard)[.-].*\.service$' | sed -E 's/\.service$//'); do
  case " $SURFACES " in *" $u "*) ;; *) SURFACES="$SURFACES $u";; esac
done

say() { printf '%s\n' "$*"; }

# ---------- 1. surface boot-order drop-ins ----------
for s in $SURFACES; do
  if [ ! -e "$U/$s.service" ] && [ ! -e "/etc/systemd/user/$s.service" ]; then
    say "SKIP  unit $s.service not found on this host"
    continue
  fi
  mkdir -p "$U/$s.service.d"
  f="$U/$s.service.d/10-lcm2-boot-order.conf"
  if grep -q 'lcm2-mnemo2-pg.service' "$f" 2>/dev/null; then
    say "OK    $s already ordered after lcm2-mnemo2-pg"
  else
    cat > "$f" <<'EOF'
[Unit]
# lcm2-pg/mnemo-pg2 boot order: engine import connects to the :54331 cluster,
# so start only after it (triangle pattern incl. After=default.target, same
# proven shape as 90-lcm-pg-boot-order.conf).
Wants=lcm2-mnemo2-pg.service
After=lcm2-mnemo2-pg.service
After=default.target
EOF
    say "FIX   $s ordered after lcm2-mnemo2-pg (10-lcm2-boot-order.conf)"
  fi
done
systemctl --user daemon-reload
# cluster unit must be enabled for boot
systemctl --user is-enabled lcm2-mnemo2-pg.service >/dev/null 2>&1 \
  && say "OK    lcm2-mnemo2-pg.service enabled" \
  || { systemctl --user enable lcm2-mnemo2-pg.service 2>/dev/null && say "FIX   enabled lcm2-mnemo2-pg.service" || say "WARN  lcm2-mnemo2-pg.service missing — run setup_instance.sh first"; }

# ---------- profile list (robust against the ◆ active-marker) ----------
profiles=$( $HERMES profile list 2>/dev/null \
  | awk 'NR>2 {gsub(/[^A-Za-z0-9_-]/,"",$1); if ($1 != "" && $1 != "Profile") print $1}' | sort -u )

# ---------- 2. threshold params ----------
if grep -q '^LCM_CONTEXT_THRESHOLD=' "$HOME/.hermes/.env" 2>/dev/null; then
  cur=$(grep '^LCM_CONTEXT_THRESHOLD=' "$HOME/.hermes/.env" | tail -1 | cut -d= -f2 | awk '{print $1}')
  [ "$cur" = "$THRESH_ENGINE" ] && say "OK    root .env LCM_CONTEXT_THRESHOLD=$cur" \
    || { printf '\nLCM_CONTEXT_THRESHOLD=%s\n' "$THRESH_ENGINE" >> "$HOME/.hermes/.env"; say "FIX   root .env pinned $THRESH_ENGINE"; }
else
  printf '\n# lcm2-pg defacto trigger: engine fires at this fraction of the window\nLCM_CONTEXT_THRESHOLD=%s\n' "$THRESH_ENGINE" >> "$HOME/.hermes/.env"
  say "FIX   root .env pinned $THRESH_ENGINE"
fi

for p in $profiles; do
  f="$HOME/.hermes/profiles/$p/.env"
  if [ -f "$f" ] && ! grep -q '^LCM_CONTEXT_THRESHOLD=' "$f"; then
    printf '\n# lcm2-pg defacto trigger (repo-owned param)\nLCM_CONTEXT_THRESHOLD=%s\n' "$THRESH_ENGINE" >> "$f"
    say "FIX   profile $p .env pinned $THRESH_ENGINE"
  fi
  if $HERMES -p "$p" config get compression.threshold >/dev/null 2>&1; then
    cur=$($HERMES -p "$p" config get compression.threshold 2>/dev/null | tail -1)
    [ "$cur" = "$THRESH_SAFEGUARD" ] \
      && say "OK    profile $p safeguard threshold $cur" \
      || { $HERMES -p "$p" config set compression.threshold "$THRESH_SAFEGUARD" >/dev/null && say "FIX   profile $p safeguard -> $THRESH_SAFEGUARD"; }
  fi
done
# ---------- 2b. mnemo-pg2 auto-lane switches (root .env) ----------
# systemd surfaces (gateway multiplex) see ONLY root .env for process env;
# profile .env is per-CLI. S1-S4 vendor-parity defaults live IN CODE
# (auto-sleep ON, context-skip ON, extraction ON, budget in meta table);
# capture/consolidate stay env-gated and are pinned ON fleet-wide here.
for kv in "MNEMO2_AUTO_CAPTURE=1" "MNEMO2_AUTO_CONSOLIDATE=1"; do
  k=${kv%%=*}
  if grep -q "^$k=" "$HOME/.hermes/.env" 2>/dev/null; then
    say "OK    root .env $k present"
  else
    printf '\n# mnemo-pg2 vendor-parity auto lanes (repo-owned)\n%s\n' "$kv" >> "$HOME/.hermes/.env"
    say "FIX   root .env pinned $kv"
  fi
done

# root config lane too (config set only — never hand-edit)
$HERMES config set compression.threshold "$THRESH_SAFEGUARD" >/dev/null 2>&1 \
  && say "FIX   root config safeguard -> $THRESH_SAFEGUARD" || true

# ---------- 3b. memory provider slot (load/register fixes from fleet work) ----------
# 'mnemo-pg2' FAILS the vendor memory-provider dir gate (silent empty slot ->
# built-in memory). The bridge name that resolves is 'mnemosyne' (repo
# plugins/mnemosyne hands the collector the PG provider from mnemo-pg2).
for p in "" $profiles; do
  [ -z "$p" ] && arg=() || arg=(-p "$p")
  cur=$($HERMES "${arg[@]}" config get memory.provider 2>/dev/null | tail -1)
  [ "$cur" = "mnemosyne" ] && continue
  $HERMES "${arg[@]}" config set memory.provider mnemosyne >/dev/null 2>&1 \
    && say "FIX   memory.provider mnemosyne (${p:-root}) [was: ${cur:-unset}]"
done

# ---------- 3c. legacy machinery disabled ----------
for p in "" $profiles; do
  [ -z "$p" ] && arg=() || arg=(-p "$p")
  for plug in lcm-pg mnemo-pg; do
    $HERMES "${arg[@]}" plugins disable "$plug" >/dev/null 2>&1 \
      && say "FIX   disabled $plug (${p:-root})" || true
  done
done
# stale probe cluster units must not crash-loop at boot
for s in lcm2-mnemo2-fix8 lcm2-mnemo2-qa6 lcm2-mnemo2-split; do
  systemctl --user is-enabled "$s.service" 2>/dev/null | grep -q enabled \
    && { systemctl --user disable --now "$s.service" 2>/dev/null; say "FIX   disabled $s.service"; } || true
done

say "DONE  apply_surface_fixes — next: bounce gateway/webui/dashboard, then run check_all.sh"
