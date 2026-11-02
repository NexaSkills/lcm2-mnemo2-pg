#!/usr/bin/env bash
# onboard_profile.sh — onboard ONE test agent to the lcm2/mnemo2 stack.
# Idempotent. Follows standard fleet profile-onboarding practice:
#   - profile created with `hermes profile create` (script creates it if missing)
#   - ALL config.yaml writes via `hermes -p <p> config set` (fleet rule; never
#     file-edit config)
#   - only the profile .env is file-edited (params)
#   - plugin dirs are SYMLINKS to the single repo source (edit once = all agents)
#   - delegation posture 1/1 (fleet standard)
#   - global-dir links ($HERMES_BASE/plugins) are OFF by DEFAULT: onboarding
#     a profile never rewires the shared global dir other profiles resolve
#     through (qa3 N-2: a fresh-clone/QA run silently deactivated the whole
#     host). Shared-host install (one repo for every profile): run with
#     LCM2_ONBOARD_GLOBAL=1 to author the global links too.
# Usage: onboard_profile.sh <agent> [--dry-run]   (or: onboard_profile.sh --dry-run
#       with no agent = list on-disk profiles and exit 0)
set -euo pipefail

ARG1=${1:?usage: onboard_profile.sh <agent> [--dry-run]}
if [ "$ARG1" = "--dry-run" ]; then AGENT=; DRY=--dry-run; else AGENT=$ARG1; DRY=${2:-}; fi
REPO=${REPO:-$(cd "$(dirname "$0")/.." && pwd)}
# Real hermes home. HERMES_HOME is profile-scoped in agent shells (points at
# ~/.hermes/profiles/<current>), so it cannot be trusted here; HERMES_REAL_HOME
# (when set) is the true base. Override HERMES_BASE for cron/off-shell use.
HERMES_BASE=${HERMES_BASE:-${HERMES_REAL_HOME:-$HOME}/.hermes}
PENV=$HERMES_BASE/profiles/$AGENT/.env
BASE=${BASE:-$HOME/lcm2-mnemo2-pg/pg}
PORT=${PORT:-54331}
ROLE=${ROLE:-mnemo201}
# F-8: our own instance's psql (fresh-clone chain must not depend on the
# sibling $HOME/lcm-pg cluster); first psql that exists wins.
PSQL_BIN=""
for _c in "$BASE/bin/psql" "$HOME/lcm2-mnemo2-pg/pg/bin/psql" "$HOME/lcm-pg/pg/bin/psql"; do
  [ -x "$_c" ] && { PSQL_BIN=$_c; break; }
done
PSQL_BIN=${PSQL_BIN:-psql}

run() { if [ "$DRY" = "--dry-run" ]; then echo "DRY: $*"; else "$@"; fi; }

echo "== 1. profile =="
if [ -z "$AGENT" ]; then
  echo "dry-run only: existing profiles on disk:"
  ls -d "$HERMES_BASE"/profiles/*/ 2>/dev/null | sed 's|.*/profiles/||; s|/$||' | grep -vE '^\.' || true
  echo "DRY done"; exit 0
fi
if [ ! -f "$HERMES_BASE/profiles/$AGENT/config.yaml" ]; then
  # A bare profiles/<agent>/ dir (no config.yaml) does NOT count as a profile:
  # `hermes -p <agent> ...` lazily generates config.yaml from defaults. Create
  # properly when absent; hermes refuses create if it considers the name taken,
  # which is fine because the materialise-by-default path below produces the
  # same blank profile.
  if ! hermes profile create "$AGENT" --description "$AGENT (lcm2-mnemo2-redesign test agent)" --no-alias 2>/dev/null; then
    echo "NOTE: hermes says profile exists (or dir stub); materialising config via CLI"
    hermes -p "$AGENT" config get context.engine >/dev/null 2>&1 || true
  fi
  [ -f "$HERMES_BASE/profiles/$AGENT/config.yaml" ] || { echo "FATAL: no config.yaml for $AGENT after create" >&2; exit 1; }
fi
[ -n "$DRY" ] && { echo "DRY done"; exit 0; }

echo "== 2. schemas on the dedicated instance =="
PSQL="$PSQL_BIN -h $BASE/run -p $PORT -U $ROLE"
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
      # NB: ${v%$'\n'} guard is load-bearing. A value that ever carries a
      # trailing newline (command substitution with echo) makes this sed's
      # replacement end in a literal newline: the KEY= line then SWALLOWS
      # the next line and the merged value is an unparseable URI
      # ('extra key/value separator' at connect, fail-closed -> sqlite
      # fallback = the trade/warren pure-sqlite class). No $() feeds this
      # today; the guard makes the silent-corruption path structurally
      # impossible regardless of caller.
      sed -i "s|^${k}=.*|${k}=${v%$'\n'}|" "$PENV"
    else
      printf '%s=%s\n' "$k" "${v%$'\n'}" >> "$PENV"
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
# Profile dir ALWAYS; the SHARED global dir only for the documented
# shared-host install (LCM2_ONBOARD_GLOBAL=1). Default profile-only so a
# QA/fresh-clone run cannot silently rewire other profiles on a shared
# host (qa3 N-2 — the F-6 class: the running clone silently deactivated
# every other profile's links).
if [ "${LCM2_ONBOARD_GLOBAL:-0}" = "1" ]; then
  LINK_DIRS=("$HERMES_BASE/plugins" "$HERMES_BASE/profiles/$AGENT/plugins")
else
  LINK_DIRS=("$HERMES_BASE/profiles/$AGENT/plugins")
  echo "NOTE: global dir $HERMES_BASE/plugins untouched (default; shared-host install: LCM2_ONBOARD_GLOBAL=1)"
fi
for d in "${LINK_DIRS[@]}"; do
  mkdir -p "$d"
  run ln -sfn "$REPO/plugins/lcm2-pg"   "$d/lcm2-pg"
  run ln -sfn "$REPO/plugins/mnemo-pg2" "$d/mnemo-pg2"
  # memory-provider DISCOVERY entry (F-1c): host's plugins/memory loader
  # finds providers by DIRECTORY name under these dirs; this neutral
  # in-repo dir bridges to the provider mnemo-pg2 publishes (plugins/
  # mnemosyne/ in the repo, authored nowhere else — F-5-clean).
  run ln -sfn "$REPO/plugins/mnemosyne" "$d/mnemosyne"
done

echo "== 6. plugin enable (idempotent; CLI, not config-file edits) =="
for pl in lcm2-pg mnemo-pg2; do
  if hermes -p "$AGENT" plugins list 2>/dev/null | grep -qE "│ *$pl .*│ enabled"; then
    echo "$pl already enabled for $AGENT"
  else
    run hermes -p "$AGENT" plugins enable "$pl"
  fi
done

echo "== 7. verify =="
[ "$DRY" = "--dry-run" ] && { echo "DRY done"; exit 0; }
hermes -p "$AGENT" config get context.engine
hermes -p "$AGENT" plugins list 2>/dev/null | grep -E "lcm2-pg|mnemo-pg2" || true
$PSQL -d lcm2db   -Atc "SELECT 1 FROM pg_namespace WHERE nspname='lcm2_$AGENT'" | grep -q 1 && echo "schema lcm2_$AGENT OK"
$PSQL -d mnemo2db -Atc "SELECT 1 FROM pg_namespace WHERE nspname='mnemo2_$AGENT'" | grep -q 1 && echo "schema mnemo2_$AGENT OK"
echo "DONE $AGENT"
