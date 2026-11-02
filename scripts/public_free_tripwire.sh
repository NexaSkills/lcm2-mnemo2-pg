#!/usr/bin/env bash
# F3 + fix7 tripwire: public schema must stay FREE of user relations on the
# lcm2 instance, in BOTH databases (lcm2db AND mnemo2db — fix7 order item 4:
# the at-rest public=0 claim must be checkable for both dbs, not just lcm2db).
# Reports only — never deletes (sweep is scripts/teardown_residue.py).
# Counterpart of the runtime invariant; catches ANY writer (rogue profile,
# maintenance script, human psql) that reintroduces public relations.
PSQL=$(command -v psql || echo /home/linuxbrew/.linuxbrew/Cellar/postgresql@17/17.10/bin/psql)
[ -x "$PSQL" ] || { echo "$(date -Is) SKIP: no psql"; exit 0; }
HOST="$HOME/lcm2-mnemo2-pg/pg/run"
fail=0
for DB in lcm2db mnemo2db; do
  DSN="postgresql:///$DB?host=$HOST&port=54331&user=mnemo201"
  n=$("$PSQL" "$DSN" -At -v ON_ERROR_STOP=1 -c \
    "select count(*) from pg_class c join pg_namespace n on n.oid=c.relnamespace
       where n.nspname='public'
         and c.relkind in ('r','p','m','f','v','pv')" 2>/dev/null)
  if [ -z "$n" ]; then
    echo "$(date -Is) ERROR: query failed on $DB (instance down? DSN=$DSN)"
    fail=1
    continue
  fi
  if [ "$n" != "0" ]; then
    fail=1
    echo "$(date -Is) TRIPWIRE: $n user relation(s) in public on $DB:"
    "$PSQL" "$DSN" -At -c \
      "select c.relkind||' '||c.relname from pg_class c
         join pg_namespace n on n.oid=c.relnamespace
        where n.nspname='public'
          and c.relkind in ('r','p','m','f','v','pv') order by 2" 2>/dev/null | sed 's/^/  /'
  else
    echo "$(date -Is) OK: $DB public table-free"
  fi
done
exit $fail
