#!/usr/bin/env bash
# setup_instance.sh — create/start the dedicated PG instance for lcm2-pg / mnemo-pg2.
# Idempotent. Owned by the invoking user; lives entirely under $HOME.
# Does NOT touch the existing lcm-pg cluster (~/lcm-pg, socket :54329).
#
# Layout (defaults; override via env):
#   BASE      = $HOME/lcm2-mnemo2-pg/pg     (data + run + log dirs)
#   PORT      = 54331                        (distinct from :54329)
#   ROLE      = mnemo201                     (cluster superuser role)
#   PGBIN     = $HOME/lcm-pg/pg/bin          (reuse private PostgreSQL install)
#   SERVICE   = lcm2-mnemo2-pg.service       (systemd USER service)
#
# IMPORTANT: every binary (initdb/postgres/psql/pg_ctl) must come from $PGBIN.
# Do not mix distro binaries (/usr/bin): a cluster is tied to the build that
# initdb'd it once WAL has been written, and distro paths put the instance
# outside the self-contained /home/mybuddy layout the plan requires. The unit
# is rewritten AND restarted when its ExecStart changes (a plain restart reuses
# a still-valid old ExecStart inode on this systemd version).
set -euo pipefail

BASE=${BASE:-$HOME/lcm2-mnemo2-pg/pg}
PORT=${PORT:-54331}
ROLE=${ROLE:-mnemo201}
PGBIN=${PGBIN:-$HOME/lcm-pg/pg/bin}
SERVICE=${SERVICE:-lcm2-mnemo2-pg.service}
DATA=$BASE/data
RUN=$BASE/run
LOG=$BASE/log
PSQL="$PGBIN/psql -h $RUN -p $PORT -U $ROLE"

echo "== 0. prereqs =="
for b in initdb postgres psql pg_ctl; do
  test -x "$PGBIN/$b" || { echo "missing $PGBIN/$b (set PGBIN)" >&2; exit 1; }
done
mkdir -p "$RUN" "$LOG"
chmod 700 "$RUN"

echo "== 1. initdb private cluster =="
if [ ! -f "$DATA/PG_VERSION" ]; then
  mkdir -p "$DATA"
  chmod 700 "$DATA"
  "$PGBIN/initdb" -D "$DATA" -U "$ROLE" --encoding=UTF8 --locale=C >/dev/null
  cat >> "$DATA/postgresql.conf" <<EOF

# --- lcm2-mnemo2-pg dedicated instance overrides (managed by this script) ---
port = $PORT
listen_addresses = ''            # local socket only
unix_socket_directories = '$RUN'
logging_collector = on
log_directory = '$LOG'
log_filename = 'postgresql-%a.log'
log_truncate_on_rotation = on
log_rotation_age = 1d
log_rotation_size = 0
shared_preload_libraries = ''
# max_connections deliberately NOT written here: the base template already
# ships the PostgreSQL default 100, and an appended heredoc key SILENTLY
# OVERRIDES it (last-one-wins) — that append once pinned 40 fleet-wide and
# caused PoolTimeout/"too many clients" storms (2026-11-09 hunt: 105 leaked
# test pools x min_size=1 hit the cap; FATAL "sorry, too many clients").
# If a host must deviate, ALTER SYSTEM (postgresql.auto.conf outranks
# postgresql.conf) — never re-add a heredoc line.
EOF
  # trust over the private 0700 socket dir: matches existing lcm-pg cluster
  # practice (its pg_hba.conf is 'local all all trust'). peer auth fails under
  # systemd user-namespace mapped peers (lcm-pg needed a pgident hack for that).
  printf 'local   all             all                                     trust\n' > "$DATA/pg_hba.conf"
fi

echo "== 2. systemd user service =="
UNIT_DIR="$HOME/.config/systemd/user"
mkdir -p "$UNIT_DIR"
UNIT="$UNIT_DIR/$SERVICE"
if [ -f "$UNIT" ] && grep -qxF "ExecStart=$PGBIN/postgres -D $DATA" "$UNIT"; then
  echo "unit already correct"
else
  systemctl --user stop "$SERVICE" 2>/dev/null || true
  cat > "$UNIT" <<EOF
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
fi
systemctl --user enable "$SERVICE" >/dev/null 2>&1 || true
systemctl --user start "$SERVICE" 2>/dev/null || systemctl --user restart "$SERVICE"
for _ in $(seq 1 30); do $PSQL -d postgres -Atc 'select 1' >/dev/null 2>&1 && break; sleep 1; done
$PSQL -d postgres -Atc "select 'connected: '||current_user||' PG '||current_setting('server_version')||' port '||current_setting('port')"
PID=$(head -1 "$DATA/postmaster.pid")
LIVE=$(readlink -f "/proc/$PID/exe")
[ "$LIVE" = "$(readlink -f "$PGBIN/postgres")" ] \
  || { echo "FATAL: postmaster binary $LIVE != $PGBIN/postgres" >&2; exit 1; }
echo "postmaster binary OK: $LIVE"

echo "== 3. databases =="
for db in lcm2db mnemo2db; do
  $PSQL -d postgres -tc "SELECT 1 FROM pg_database WHERE datname='$db'" | grep -q 1 \
    || $PSQL -d postgres -c "CREATE DATABASE $db OWNER $ROLE"
done

echo "== 4. per-agent schemas (idempotent; pass agent names as args) =="
# lcm2db: private schemas ONLY (lcm2_<agent>, NO shared schema).
# mnemo2db: private schemas (mnemo2_<agent>) PLUS the cross-agent 'shared' schema.
$PSQL -d mnemo2db -c "CREATE SCHEMA IF NOT EXISTS \"shared\"" >/dev/null
for agent in "$@"; do
  # W3TKT-MIKE-1220 #3: quoted identifiers — hyphenated profile ids are
  # valid (hermes PROFILE_ID_RE) but a bare lcm2_demo-freshkey token is a
  # SQL syntax error (same cure as onboard_profile.sh §2).
  $PSQL -d lcm2db   -c "CREATE SCHEMA IF NOT EXISTS \"lcm2_$agent\""
  $PSQL -d mnemo2db -c "CREATE SCHEMA IF NOT EXISTS \"mnemo2_$agent\""
done

echo "== 5. sanity =="
$PSQL -d postgres -Atc "SELECT datname FROM pg_database WHERE NOT datistemplate ORDER BY 1"
$PSQL -d lcm2db   -Atc "SELECT nspname FROM pg_namespace WHERE nspname LIKE 'lcm2%' ORDER BY 1"
$PSQL -d mnemo2db -Atc "SELECT nspname FROM pg_namespace WHERE nspname LIKE 'mnemo2%' OR nspname='shared' ORDER BY 1"
echo "DSN_LCM2=postgresql://$ROLE@localhost:$PORT/lcm2db?host=$RUN"
echo "DSN_MNEMO2=postgresql://$ROLE@localhost:$PORT/mnemo2db?host=$RUN"

echo "== 6. bootstrap assertion (never trust the exit code) =="
# A LISTENing :54331 whose initdb leg silently no-oped looks healthy from
# `systemctl is-active` yet answers 'role does not exist' for every real
# login (fresh-host roll dies pure-sqlite: same family as the T-2 lesson).
# Fail the SETUP here, deterministically, so no fresh-host roll proceeds on a
# cluster with no bootstrap state.
bash "$(dirname "$0")/bootstrap_verify.sh" "$@" || exit 1
echo "DONE"
