#!/usr/bin/env bash
# lcm2-mnemo2-ensure.sh — watchdog for the Phase-1 dedicated PG instance.
# Self-heals the three things a rogue process can break: role mnemo201,
# pg_hba trust, and lcm2db/mnemo2db + agent schemas. Never touches :54329.
set -uo pipefail
BASE=${BASE:-$HOME/lcm2-mnemo2-pg/pg}
PORT=${PORT:-54331}
ROLE=${ROLE:-mnemo201}
PGBIN=${PGBIN:-$HOME/lcm-pg/pg/bin}
UNIT=lcm2-mnemo2-pg.service
LOG=$HOME/.hermes/logs/lcm2-mnemo2-ensure.log
note(){ echo "$(date -Is) $*" >> "$LOG"; }

systemctl --user start $UNIT 2>/dev/null
sleep 1
PSQL="$PGBIN/psql -h $BASE/run -p $PORT -U $ROLE -d postgres"
if ! $PSQL -Atc 'select 1' >/dev/null 2>&1; then
  # role or hba broken: offline-fix via single-user backend (no other server
  # may be running against this DATA dir; the unit is stopped first).
  systemctl --user stop $UNIT; sleep 1
  if grep -qE "^local.*peer" "$BASE/data/pg_hba.conf"; then
    printf 'local   all             all                                     trust\n' > "$BASE/data/pg_hba.conf"
    note "repaired pg_hba -> trust"
  fi
  if ! grep -q "^local" "$BASE/data/pg_hba.conf"; then
    printf 'local   all             all                                     trust\n' > "$BASE/data/pg_hba.conf"
    note "restored pg_hba"
  fi
  printf "CREATE ROLE $ROLE SUPERUSER LOGIN;\n" | env -u PGHOST -u PGPORT -u PGUSER -u PGDATABASE \
    "$PGBIN/postgres" --single -D "$BASE/data" -F postgres >/dev/null 2>&1
  note "ensure role $ROLE (single-user)"
  systemctl --user start $UNIT; sleep 2
fi
for db in lcm2db mnemo2db; do
  $PSQL -tc "SELECT 1 FROM pg_database WHERE datname='$db'" 2>/dev/null | grep -q 1 \
    || { $PSQL -c "CREATE DATABASE $db OWNER $ROLE" >>"$LOG" 2>&1; note "created db $db"; }
done
$PSQL -d lcm2db   -c "CREATE SCHEMA IF NOT EXISTS lcm2_lcm2agent"   >/dev/null 2>&1
$PSQL -d mnemo2db -c "CREATE SCHEMA IF NOT EXISTS mnemo2_mnemo2agent" >/dev/null 2>&1
$PSQL -d mnemo2db -c "CREATE SCHEMA IF NOT EXISTS shared"           >/dev/null 2>&1
if $PSQL -d postgres -Atc 'select 1' >/dev/null 2>&1; then note "OK instance healthy"; else note "FAIL instance unreachable"; fi
tail -200 "$LOG" > "$LOG.tmp" && mv "$LOG.tmp" "$LOG"
