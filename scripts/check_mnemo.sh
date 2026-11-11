#!/usr/bin/env bash
# check_mnemo.sh — READ-ONLY verification of the mnemo-pg2 deployment on
# this host. Same philosophy as the lcm2 check lane: every line is
# OK/WARN/FAIL, exit 0 only with zero FAILs. No writes, no service touches.
set -uo pipefail
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PORT=${PORT:-54331}
RUNDIR=${RUNDIR:-$HOME/lcm2-mnemo2-pg/pg/run}
ROLE=${ROLE:-mnemo201}
DB=${DB:-mnemo2db}
FAILS=0; WARNS=0
ok()   { printf 'OK    %s\n' "$*"; }
warn() { printf 'WARN  %s\n' "$*"; WARNS=$((WARNS+1)); }
fail() { printf 'FAIL  %s\n' "$*"; FAILS=$((FAILS+1)); }
psql_q() { psql -h "$RUNDIR" -p "$PORT" -U "$ROLE" -d "$DB" -Atc "$1" 2>/dev/null; }

# 1. instance alive
if [ "$(psql_q 'SELECT 1')" = 1 ]; then ok "pg instance :$PORT reachable"
else fail "pg instance :$PORT unreachable (run scripts/ensure_instance.sh)"; fi

# 2. per-schema health
mapfile -t SCH < <(psql_q "SELECT nspname FROM pg_namespace WHERE nspname LIKE 'mnemo2%' OR nspname='shared' ORDER BY 1")
[ ${#SCH[@]} -gt 0 ] || fail "no mnemo2_* schemas found"
for s in "${SCH[@]}"; do
  [ -n "$s" ] || continue
  if [ "$(psql_q "SELECT to_regclass('$s.memory_embeddings') IS NOT NULL")" != t ]; then
    warn "$s: no memory_embeddings (never bootstrapped?)"; continue
  fi
  dim=$(psql_q "SELECT atttypmod FROM pg_attribute a JOIN pg_class c ON c.oid=a.attrelid JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='$s' AND c.relname='memory_embeddings' AND a.attname='embedding'")
  if [ "${dim:-0}" = 384 ] || [ "${dim:-0}" = 256 ]; then ok "$s: embedding dim ${dim}"; else fail "$s: unexpected embedding dim ${dim:-?}"; fi
  nn=$(psql_q "SELECT count(*) FROM $s.memory_embeddings WHERE embedding IS NULL")
  [ "${nn:-1}" = 0 ] && ok "$s: 0 NULL vectors" || warn "$s: $nn NULL vectors (run embed_migrate.sh)"
  hn=$(psql_q "SELECT count(*) FROM pg_indexes WHERE schemaname='$s' AND indexdef ILIKE '%hnsw%'")
  [ "${hn:-0}" -ge 1 ] && ok "$s: HNSW index present" || warn "$s: no HNSW index (run embed_migrate.sh)"
  sem=$(psql_q "SELECT count(*) FROM $s.memory_embeddings WHERE model LIKE 'fastembed%'")
  tot=$(psql_q "SELECT count(*) FROM $s.memory_embeddings")
  if [ "${tot:-0}" -gt 0 ] && [ "${sem:-0}" = 0 ]; then warn "$s: 0/$tot rows have semantic vectors (pre-migration? run embed_migrate.sh)"; else ok "$s: semantic tags ${sem:-0}/${tot:-0}"; fi
done

# 3. shared views resolve to the shared schema (bake check)
baked=$(psql_q "SELECT COUNT(*) FROM pg_views WHERE viewname='shared_embeddings' AND definition LIKE '%shared.memory_embeddings%'")
npriv=$(psql_q "SELECT count(*) FROM pg_namespace WHERE nspname LIKE 'mnemo2%'")
if [ "${baked:-0}" -ge "$npriv" ] && [ "$npriv" -gt 0 ]; then ok "shared_embeddings baked in $baked/$npriv private schemas"
else warn "shared_embeddings baked only $baked/$npriv (connect bakes on first use; check bake lane)"; fi

# 4. fastembed AND psycopg importable in EVERY installed env (X-4 class:
# hermes update can rebuild envs without psycopg -> engines fail-loud)
nenv=0; nbad=0; npbad=0
for pyc in "$HOME"/.hermes/installs/*/environments/*/venv/bin/python; do
  [ -x "$pyc" ] || continue
  nenv=$((nenv+1))
  "$pyc" -c "import fastembed" 2>/dev/null || { nbad=$((nbad+1)); echo "      missing fastembed: ${pyc%/bin/python}"; }
  "$pyc" -c "import psycopg, psycopg_pool" 2>/dev/null || { npbad=$((npbad+1)); echo "      missing psycopg: ${pyc%/bin/python}"; }
done
if [ "$nenv" = 0 ]; then warn "no installed venvs found (hermes not installed here?)"
else
  [ "$nbad" = 0 ] && ok "fastembed importable in all $nenv installed envs" \
    || fail "fastembed missing in $nbad/$nenv installed envs (gateway env may be one of them)"
  [ "$npbad" = 0 ] && ok "psycopg importable in all $nenv installed envs" \
    || fail "psycopg missing in $npbad/$nenv installed envs (X-4 class — run apply_surface_fixes.sh)"
fi

# 5. model cache provisioned somewhere warmable (cache dir env or repo default)
CACHE=${MNEMO2_EMBED_CACHE:-$HOME/lcm2-mnemo2-pg/models/fastembed}
if [ -d "$CACHE/models--Qdrant--bge-small-en-v1.5-onnx-Q" ]; then ok "bge-small ONNX cache present"
else warn "model cache absent ($CACHE) — first load will need network (~13 s)"; fi

# 6. plugin posture (root config lane only; read via hermes config get)
HERMES=${HERMES:-hermes}
dis=$($HERMES config get plugins.disabled 2>/dev/null || true)
echo "$dis" | grep -q "lcm-pg" && ok "legacy lcm-pg disabled (root)" || warn "legacy lcm-pg NOT disabled (root)"
# vendor mnemosyne must not be LOADED — but NOTE: this repo itself ships
# a 'mnemosyne' BRIDGE plugin (collector provider handoff); name in the
# list is fine as long as it resolves INSIDE this repo. Provenance (the
# symlink target), not the name, decides co-existence.
if $HERMES plugins list 2>/dev/null | grep -E "^\S*\s*│?\s*mnemosyne" | grep -q enabled; then
  tgt=$(readlink -f "$HOME/.hermes/plugins/mnemosyne" 2>/dev/null)
  case "$tgt" in
    "$REPO_DIR/plugins/mnemosyne"|"$HOME"/lcm2-mnemo2-pg/plugins/mnemosyne)
      ok "mnemosyne loaded = repo bridge (co-existence rule holds)" ;;
    "")
      warn "mnemosyne listed enabled but no plugins-dir link found (source unclear — inspect manually)" ;;
    *)
      fail "mnemosyne loaded from NON-REPO path: $tgt (vendor co-existence!)" ;;
  esac
else
  ok "vendor mnemosyne not loaded (co-existence rule holds)"
fi

# 7. boot ordering drop-in (gateway waits for the pg instance) — unit
# names vary by host layout (beez: hermes-gateway; trade/template:
# lane-variant -default/-alpha/-demo), so probe every gateway variant.
GWS=$(systemctl --user list-unit-files 2>/dev/null | awk '/^hermes-gateway/ {print $1}')
if [ -z "$GWS" ]; then ok "no hermes-gateway units on this host (nothing to order)"
else
  found=0
  for u in $GWS; do
    systemctl --user cat "$u" 2>/dev/null | grep -q "lcm2-mnemo2-pg" && found=1
  done
  [ "$found" = 1 ] && ok "gateway boot drop-in ordered on lcm2-mnemo2-pg" \
    || warn "gateway boot drop-in missing on: $(echo $GWS | tr '\n' ' ') (apply_surface_fixes.sh)"
fi

# 8. plugin-placement STANDARD (Jan 2026-11-11): real-dir hub under
# ~/.hermes/plugins, profile links via the hub, checkout under ~/.hermes
# on self-contained hosts. Read-only verdict from plugin_hub.sh --check.
SC=0; grep -q "^LCM2_SELF_CONTAINED=1" "$HOME/.hermes/.env" 2>/dev/null && SC=1
if [ -x "$REPO_DIR/scripts/plugin_hub.sh" ] || [ -f "$REPO_DIR/scripts/plugin_hub.sh" ]; then
  ph=$(PLUGIN_HUB_RELOCATE=$SC bash "$REPO_DIR/scripts/plugin_hub.sh" --check 2>&1); prc=$?
  if [ "$prc" = 0 ]; then ok "plugin hub standard holds (plugin_hub --check)"
  else
    warn "plugin hub drift (run update_mnemo_host.sh):"
    echo "$ph" | grep -E "^(FAIL|FIX)" | sed 's/^/      /'
  fi
fi

echo "----"
echo "check_mnemo: FAIL=$FAILS WARN=$WARNS"
[ "$FAILS" = 0 ]
