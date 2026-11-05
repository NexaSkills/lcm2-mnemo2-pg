#!/usr/bin/env bash
# consolidate_stale.sh — P4-C hygiene pass for IDLE hosts.
#
# Session-end auto-consolidation (MNEMO2_AUTO_CONSOLIDATE=1) only fires
# when a profile actually ends sessions. This timer covers hosts that
# sit idle: one engine.sleep() pass per profile (>24h-old unconsolidated
# rows, markers exclude repeats, originals kept lossless).
#
# Idempotent + fail-soft; safe to run from a systemd timer hourly.
# Install (manual, per host — YAGNI until proven needed fleet-wide):
#   systemd-run --user --on-calendar=hourly --collect \
#     --timer-name=mnemo2-consolidate.timer ~/lcm2-mnemo2-pg/scripts/consolidate_stale.sh
set -u
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"

# Interpreter: the launcher's ACTIVE env python (installs/*/facts.json
# pointer). Fall back to PATH python3 (needs psycopg) — never a stale env.
PY=""
for f in "$HERMES_HOME"/installs/*/environments/*/facts.json; do
  [ -f "$f" ] || continue
  if grep -q '"active"[^,]*true' "$f" 2>/dev/null; then
    cand="$(dirname "$f")/venv/bin/python"
    [ -x "$cand" ] && PY="$cand" && break
  fi
done
[ -n "$PY" ] || PY="$(command -v python3)"
"$PY" -c "import psycopg" 2>/dev/null || { echo "no psycopg in $PY — skip" >&2; exit 0; }

for envf in "$HERMES_HOME/.env" "$HERMES_HOME"/profiles/*/.env; do
  [ -f "$envf" ] || continue
  grep -q '^MNEMO2_PG_DSN=' "$envf" 2>/dev/null || continue
  profile="$(basename "$(dirname "$envf")")"
  [ "$envf" = "$HERMES_HOME/.env" ] && profile=default
  ( set -a; . "$envf" 2>/dev/null; set +a
    MNEMO2_AGENT_NAME="$profile" \
    PYTHONPATH="$REPO/plugins/mnemo-pg2" \
    "$PY" -c "
import sys; sys.path.insert(0,'$REPO/plugins/mnemo-pg2')
import engine
print('$profile:', engine.Engine().sleep())
" 2>&1 | tail -1
  )
done
exit 0
