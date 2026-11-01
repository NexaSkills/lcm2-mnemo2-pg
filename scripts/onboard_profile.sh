#!/usr/bin/env bash
# onboard_profile.sh — onboard ONE test agent to the lcm2/mnemo2 stack.
# Idempotent. Follows standard fleet profile-onboarding practice:
#   - profile created with `hermes profile create` (script creates it if missing)
#   - ALL config.yaml writes via `hermes -p <p> config set` (fleet rule; never
#     file-edit config)
#   - only the profile .env is file-edited (params)
#   - plugin dirs are SYMLINKS to the single repo source (edit once = all agents)
#   - delegation posture 1/1 (fleet standard)
# Usage: onboard_profile.sh <agent> [--dry-run]
set -euo pipefail

AGENT=${1:?usage: onboard_profile.sh <agent> [--dry-run]}
DRY=${2:-}
REPO=${REPO:-$(cd "$(dirname "$0")/.." && pwd)}
HERMES_HOME=${HERMES_HOME:-$HOME/.hermes}
PENV=$HERMES_HOME/profiles/$AGENT/.env
BASE=${BASE:-$HOME/lcm2-mnemo2-pg/pg}
PORT=${PORT:-54331}
ROLE=${ROLE:-mnemo201}

run() { if [ "$DRY" = "--dry-run" ]; then echo "DRY: $*"; else "$@"; fi; }

echo "== 1. profile =="
if [ ! -d "$HERMES_HOME/profiles/$AGENT" ]; then
  run hermes profile create "$AGENT" --description "$AGENT (lcm2-mnemo2-redesign test agent)" --no-alias
fi
[ -n "$DRY" ] && { echo "DRY done"; exit 0; }

echo "== 2. schemas on the dedicated instance =="
PSQL="$HOME/lcm-pg/pg/bin/psql -h $BASE/run -p $PORT -U $ROLE"
$PSQL -d lcm2db   -c "CREATE SCHEMA IF NOT EXISTS lcm2_$AGENT"
$PSQL -d mnemo2db -c "CREATE SCHEMA IF NOT EXISTS mnemo2_$AGENT"
$PSQL -d mnemo2db -c "CREATE SCHEMA IF NOT EXISTS shared"

echo "== 3. profile .env params (file-editable surface) =="
if [ "$DRY" = "--dry-run" ]; then
  echo "DRY: append LCM2_*/MNEMO2_* params to $PENV"
else
  touch "$PENV"; chmod 600 "$PENV"
  set_param() {  # set_param KEY VALUE — in-place update or append
    local k=$1 v=$2
    if grep -q "^${k}=" "$PENV"; then
      sed -i "s|^${k}=.*|${k}=${v}|" "$PENV"
    else
      printf '%s=%s\n' "$k" "$v" >> "$PENV"
    fi
  }
  # NOTE: schema is derived by convention from the agent name (lcm2_<agent> /
  # mnemo2_<agent>), NOT pinned as a DSN param — the plugins derive it per
  # profile at runtime (fleet practice: never pin the schema).
  set_param LCM2_PG_DSN      "postgresql:///lcm2db?host=$BASE/run&port=$PORT&user=$ROLE"
  set_param MNEMO2_PG_DSN    "postgresql:///mnemo2db?host=$BASE/run&port=$PORT&user=$ROLE"
  set_param MNEMO2_SHARED_SCHEMA shared
fi

echo "== 4. config via hermes config set ONLY =="
run hermes -p "$AGENT" config set context.engine lcm
run hermes -p "$AGENT" config set memory.provider mnemosyne
run hermes -p "$AGENT" config set delegation.max_concurrent_children 1
run hermes -p "$AGENT" config set delegation.max_spawn_depth 1

echo "== 5. plugin symlinks (single source in this repo) =="
for d in "$HERMES_HOME/plugins" "$HERMES_HOME/profiles/$AGENT/plugins"; do
  mkdir -p "$d"
  run ln -sfn "$REPO/plugins/lcm2-pg"   "$d/lcm2-pg"
  run ln -sfn "$REPO/plugins/mnemo-pg2" "$d/mnemo-pg2"
done

echo "== 6. verify =="
[ "$DRY" = "--dry-run" ] && { echo "DRY done"; exit 0; }
hermes -p "$AGENT" config get context.engine
hermes -p "$AGENT" config get memory.provider
$PSQL -d lcm2db   -Atc "SELECT 1 FROM pg_namespace WHERE nspname='lcm2_$AGENT'" | grep -q 1 && echo "schema lcm2_$AGENT OK"
$PSQL -d mnemo2db -Atc "SELECT 1 FROM pg_namespace WHERE nspname='mnemo2_$AGENT'" | grep -q 1 && echo "schema mnemo2_$AGENT OK"
echo "DONE $AGENT"
