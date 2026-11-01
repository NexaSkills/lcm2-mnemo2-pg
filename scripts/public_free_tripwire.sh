#!/usr/bin/env bash
# F3 tripwire: public schema must stay FREE of user tables on the lcm2 instance.
# Reports only — never deletes (sweep is scripts/sweep_public_residue.py).
# Counterpart of the runtime invariant in scripts/sweep_public_residue.py;
# catches ANY writer (rogue profile, maintenance script, human psql) that
# reintroduces public relations between sweeps.
PSQL=$(command -v psql || echo /home/linuxbrew/.linuxbrew/Cellar/postgresql@17/17.10/bin/psql)
[ -x "$PSQL" ] || { echo "$(date -Is) SKIP: no psql"; exit 0; }
DSN="postgresql:///lcm2db?host=$HOME/lcm2-mnemo2-pg/pg/run&port=54331&user=mnemo201"
n=$("$PSQL" "$DSN" -At -v ON_ERROR_STOP=1 -c \
  "select count(*) from pg_class c join pg_namespace n on n.oid=c.relnamespace
     where n.nspname='public'
       and c.relkind in ('r','p','m','f','v','pv')" 2>/dev/null)
if [ -z "$n" ]; then
  echo "$(date -Is) ERROR: query failed (instance down? DSN=$DSN)"
  exit 0
fi
if [ "$n" != "0" ]; then
  echo "$(date -Is) TRIPWIRE: $n user relation(s) in public on lcm2 instance:"
  "$PSQL" "$DSN" -At -c \
    "select c.relkind||' '||c.relname from pg_class c
       join pg_namespace n on n.oid=c.relnamespace
      where n.nspname='public'
        and c.relkind in ('r','p','m','f','v','pv') order by 2" 2>/dev/null | sed 's/^/  /'
else
  echo "$(date -Is) OK: public table-free"
fi
