#!/usr/bin/env bash
# setup_dbs.sh — idempotently create lcm2db + mnemo2db, the cluster role, and
# the per-agent schemas on the DEDICATED lcm2-mnemo2 instance (socket
# ~/lcm2-mnemo2-pg/pg/run, port 54331). NEVER connects to :54329 (lcm-pg).
#
# Schema pattern per plan (binding):
#   lcm2db   : lcm2_<agent>  (private only — NO shared schema, R2)
#   mnemo2db : mnemo2_<agent> (private) + `shared` (cross-agent, mnemo2db only)
# Usage: setup_dbs.sh [agent ...]   (defaults: lcm2agent mnemo2agent)
set -euo pipefail
BASE=${BASE:-$HOME/lcm2-mnemo2-pg/pg}
PORT=${PORT:-54331}
ROLE=${ROLE:-mnemo201}
PGBIN=${PGBIN:-$HOME/lcm-pg/pg/bin}
AGENTS=("$@"); [ ${#AGENTS[@]} -eq 0 ] && AGENTS=(lcm2agent mnemo2agent)

# ensure the instance is up (start unit if inactive)
systemctl --user start lcm2-mnemo2-pg.service 2>/dev/null || true
PSQL="${PSQL:-$PGBIN/psql -h $BASE/run -p $PORT -U $ROLE -d postgres}"
for i in $(seq 1 20); do $PSQL -Atc 'select 1' >/dev/null 2>&1 && break; sleep 1; done
$PSQL -Atc 'select 1' >/dev/null || { echo "instance not reachable on $BASE/run:$PORT" >&2; exit 1; }

for db in lcm2db mnemo2db; do
  $PSQL -tAc "SELECT 1 FROM pg_database WHERE datname='$db'" | grep -q 1 \
    && echo "db $db exists" || { $PSQL -qc "CREATE DATABASE $db OWNER $ROLE"; echo "created db $db"; }
done

$PSQL -d mnemo2db -qc "CREATE SCHEMA IF NOT EXISTS \"shared\""
for a in "${AGENTS[@]}"; do
  # W3TKT-MIKE-1220 #3: quoted identifiers (hyphenated profile ids are
  # valid; a bare lcm2_<name-with-dash> is a SQL syntax error).
  $PSQL -d lcm2db   -qc "CREATE SCHEMA IF NOT EXISTS \"lcm2_$a\""
  $PSQL -d mnemo2db -qc "CREATE SCHEMA IF NOT EXISTS \"mnemo2_$a\""
done

echo "== verify =="
$PSQL -d lcm2db -Atc "SELECT nspname FROM pg_namespace WHERE nspname LIKE 'lcm2%' ORDER BY 1"
if $PSQL -d lcm2db -Atc "SELECT nspname FROM pg_namespace WHERE nspname='shared'" | grep -q shared; then
  echo "FAIL: shared schema must NOT exist in lcm2db (R2)" >&2; exit 1
fi
$PSQL -d mnemo2db -Atc "SELECT nspname FROM pg_namespace WHERE nspname LIKE 'mnemo2%' OR nspname='shared' ORDER BY 1"
echo DONE
