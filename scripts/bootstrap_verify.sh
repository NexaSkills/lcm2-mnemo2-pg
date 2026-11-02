#!/usr/bin/env bash
# bootstrap_verify.sh — bootstrap-time assertion for the dedicated
# lcm2-mnemo2 instance (never trust the exit code: a LISTENing :54331 with no
# usable bootstrap state looks identical to a healthy one from `systemctl
# is-active`). Run AFTER setup_instance.sh / setup_dbs.sh / the unit start on
# any host; exit non-zero if the cluster bootstrap leg silently no-oped.
#
# Checks (all against the DEDICATED instance only, never :54329):
#   A1  cluster answers on $BASE/run :$PORT as $ROLE (the role initdb authors)
#   A2  databases lcm2db + mnemo2db exist
#   A3  schemas: shared (mnemo2db) + lcm2_<agent>/mnemo2_<agent> for every
#       agent passed as an argument (empty-but-present is fine per kit §2;
#       DDL materializes on first write)
#   A4  postmaster sanity: exactly ONE postmaster holds this data dir and its
#       binary == $PGBIN/postgres (stale/foreign postmaster = crash-loop class)
#   A5  pg_hba is trust over the private socket dir (initdb authors this;
#       peer auth fails under systemd user-namespace mapped peers)
# Usage: bootstrap_verify.sh [agent ...]   (same agent list as setup_instance.sh)
# Env overrides mirror setup_instance.sh: BASE PORT ROLE PGBIN.
set -uo pipefail
BASE=${BASE:-$HOME/lcm2-mnemo2-pg/pg}
PORT=${PORT:-54331}
ROLE=${ROLE:-mnemo201}
PGBIN=${PGBIN:-$HOME/lcm-pg/pg/bin}
DATA=$BASE/data
RUN=$BASE/run
AGENTS=("$@")
PSQL="$PGBIN/psql -h $RUN -p $PORT -U $ROLE -v ON_ERROR_STOP=1"
FAIL=0
fail(){ echo "BOOTSTRAP_FAIL: $*" >&2; FAIL=1; }
ok(){ echo "OK $*"; }

# A1 — role + reachability. 'role does not exist' here = initdb never
# completed under -U $ROLE (the ONLY bootstrap leg that authors the role).
if ! $PSQL -d postgres -Atc 'select 1' >/dev/null 2>&1; then
  # surface WHY (pg_hba reject vs role missing vs socket dead) for the log
  err=$($PSQL -d postgres -Atc 'select 1' 2>&1 | head -1)
  fail "A1 instance unreachable as role $ROLE on $RUN:$PORT — $err"
  exit 1
fi
cu=$($PSQL -d postgres -Atc 'select current_user')
[ "$cu" = "$ROLE" ] || fail "A1 connected as '$cu', expected bootstrap role '$ROLE'"
port_now=$($PSQL -d postgres -Atc 'show port' 2>/dev/null)
[ "$port_now" = "$PORT" ] || fail "A1 server port $port_now != $PORT (wrong cluster on this socket dir?)"

# A2 — databases
for db in lcm2db mnemo2db; do
  [ "$($PSQL -d postgres -Atc "SELECT 1 FROM pg_database WHERE datname='$db'")" = 1 ] \
    && ok "A2 db $db" || fail "A2 db $db missing (setup_dbs/setup_instance db leg no-oped)"
done

# A3 — schemas
[ "$($PSQL -d mnemo2db -Atc "SELECT 1 FROM pg_namespace WHERE nspname='shared'" 2>/dev/null)" = 1 ] \
  && ok "A3 schema shared (mnemo2db)" || fail "A3 schema 'shared' missing in mnemo2db"
for a in "${AGENTS[@]}"; do
  [ "$($PSQL -d lcm2db -Atc "SELECT 1 FROM pg_namespace WHERE nspname='lcm2_$a'" 2>/dev/null)" = 1 ] \
    && ok "A3 schema lcm2_$a" || fail "A3 schema lcm2_$a missing in lcm2db"
  [ "$($PSQL -d mnemo2db -Atc "SELECT 1 FROM pg_namespace WHERE nspname='mnemo2_$a'" 2>/dev/null)" = 1 ] \
    && ok "A3 schema mnemo2_$a" || fail "A3 schema mnemo2_$a missing in mnemo2db"
done
# R2 invariant: NO shared schema in lcm2db
[ "$($PSQL -d lcm2db -Atc "SELECT 1 FROM pg_namespace WHERE nspname='shared'" 2>/dev/null)" = 1 ] \
  && fail "A3 'shared' must NOT exist in lcm2db (R2)" || ok "A3 lcm2db has no shared (R2)"

# A4 — postmaster: single owner of DATA, binary from PGBIN
PIDFILE=$DATA/postmaster.pid
if [ -f "$PIDFILE" ]; then
  PID=$(head -1 "$PIDFILE" 2>/dev/null)
  LIVE=$(readlink -f "/proc/$PID/exe" 2>/dev/null)
  [ "$LIVE" = "$(readlink -f "$PGBIN/postgres" 2>/dev/null)" ] \
    && ok "A4 postmaster $PID binary $LIVE" \
    || fail "A4 postmaster pid $PID binary '${LIVE:-gone}' != $PGBIN/postgres"
  n_owners=$(pgrep -a postgres 2>/dev/null | grep -c -- "-D $DATA" || true)
  [ "${n_owners:-0}" -le 1 ] || fail "A4 $n_owners postmasters hold $DATA (stale/foreign = crash-loop class)"
else
  fail "A4 no $PIDFILE (cluster not started from $DATA)"
fi

# A5 — auth mode
grep -qE '^local[[:space:]]+all[[:space:]]+all[[:space:]]+trust' "$DATA/pg_hba.conf" 2>/dev/null \
  && ok "A5 pg_hba trust" || fail "A5 pg_hba lacks 'local all all trust' (peer auth breaks under user-ns mapped peers)"

if [ "$FAIL" = 0 ]; then echo "BOOTSTRAP_VERIFY OK (role=$cu db=2 agents=${#AGENTS[@]} port=$PORT socket=$RUN)"; exit 0
else echo "BOOTSTRAP_VERIFY FAILED (see BOOTSTRAP_FAIL lines above)"; exit 1; fi
