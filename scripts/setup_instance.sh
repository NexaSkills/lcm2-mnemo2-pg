#!/usr/bin/env bash
# setup_instance.sh — create the dedicated PG instance for lcm2-pg / mnemo-pg2.
# Idempotent. Owned by the invoking user; lives entirely under $HOME.
# Does NOT touch the existing lcm-pg cluster (~/lcm-pg, socket :54329).
#
# Layout (defaults; override via env):
#   BASE      = $HOME/lcm2-mnemo2-pg/pg     (data + run dirs)
#   PORT      = 54331                        (distinct from :54329)
#   ROLE      = mnemo201                     (cluster superuser role)
#   PGBIN     = $HOME/lcm-pg/pg/bin          (reuse private PostgreSQL install)
#   SERVICE   = lcm2-mnemo2-pg.service       (systemd USER service)
set -euo pipefail

BASE=${BASE:-$HOME/lcm2-mnemo2-pg/pg}
PORT=${PORT:-54331}
ROLE=${ROLE:-mnemo201}
PGBIN=${PGBIN:-$HOME/lcm-pg/pg/bin}
SERVICE=${SERVICE:-lcm2-mnemo2-pg.service}
DATA=$BASE/data
RUN=$BASE/run
PSQL="$PGBIN/psql -h $RUN -p $PORT -U $ROLE"

echo "== 0. prereqs =="
for b in initdb postgres psql pg_ctl; do
  test -x "$PGBIN/$b" || { echo "missing $PGBIN/$b (set PGBIN)" >&2; exit 1; }
done

echo "== 1. initdb private cluster =="
if [ ! -f "$DATA/PG_VERSION" ]; then
  mkdir -p "$DATA" "$RUN"
  chmod 700 "$DATA"
  "$PGBIN/initdb" -D "$DATA" -U "$ROLE" --auth=peer --encoding=UTF8 --locale=C >/dev/null
  cat >> "$DATA/postgresql.conf" <<EOF

# --- lcm2-mnemo2-pg dedicated instance overrides ---
port = $PORT
listen_addresses = ''            # local socket only
unix_socket_directories = '$RUN'
shared_preload_libraries = ''
max_connections = 40
EOF
  # trust over the private 0700 socket dir: matches existing lcm-pg cluster
  # practice (its pg_hba.conf is 'local all all trust').
  printf 'local   all             all                                     trust\n' > "$DATA/pg_hba.conf"
fi

echo "== 2. systemd user service =="
UNIT_DIR="$HOME/.config/systemd/user"
mkdir -p "$UNIT_DIR"
cat > "$UNIT_DIR/$SERVICE" <<EOF
[Unit]
Description=LCM2/MNEMO2 dedicated PostgreSQL cluster (self-contained, socket $PORT)

[Service]
Type=simple
ExecStart=$PGBIN/postgres -D $DATA
Restart=on-failure
RestartSec=2
TimeoutStopSec=30

[Install]
WantedBy=default.target
EOF
systemctl --user daemon-reload
systemctl --user enable --now "$SERVICE" 2>/dev/null || systemctl --user restart "$SERVICE"
for _ in $(seq 1 30); do $PSQL -d postgres -Atc 'select 1' >/dev/null 2>&1 && break; sleep 1; done
$PSQL -d postgres -c 'select version();' | head -2

echo "== 3. databases =="
for db in lcm2db mnemo2db; do
  $PSQL -d postgres -tc "SELECT 1 FROM pg_database WHERE datname='$db'" | grep -q 1 \
    || $PSQL -d postgres -c "CREATE DATABASE $db OWNER $ROLE"
done

echo "== 4. per-agent schemas (idempotent; pass agent names as args) =="
# lcm2db: private schemas ONLY (lcm2_<agent>, NO shared schema).
# mnemo2db: private schemas (mnemo2_<agent>) PLUS the cross-agent 'shared' schema.
$PSQL -d mnemo2db -c "CREATE SCHEMA IF NOT EXISTS shared" >/dev/null
for agent in "$@"; do
  $PSQL -d lcm2db   -c "CREATE SCHEMA IF NOT EXISTS lcm2_$agent"
  $PSQL -d mnemo2db -c "CREATE SCHEMA IF NOT EXISTS mnemo2_$agent"
done

echo "== 5. sanity =="
$PSQL -d postgres -Atc "SELECT datname FROM pg_database WHERE NOT datistemplate ORDER BY 1"
$PSQL -d lcm2db   -Atc "SELECT nspname FROM pg_namespace WHERE nspname LIKE 'lcm2%' ORDER BY 1"
$PSQL -d mnemo2db -Atc "SELECT nspname FROM pg_namespace WHERE nspname LIKE 'mnemo2%' OR nspname='shared' ORDER BY 1"
echo "DSN_LCM2=postgresql:///lcm2db?host=$RUN&port=$PORT&user=$ROLE"
echo "DSN_MNEMO2=postgresql:///mnemo2db?host=$RUN&port=$PORT&user=$ROLE"
echo "DONE"
