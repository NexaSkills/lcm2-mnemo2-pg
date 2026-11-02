"""Teardown + persistent public sweep for Phase A fix6 (token LCM2-MNEMO2-PHASEA-FIX6).

The PERSISTENT-state half of the N-3 root fix (qa4 rec #1): a public table
that exists AT REST is re-published into every freshly created schema by the
mnemo-pg2 shared-view bake (mnemo-pg2/pgconn.py: `CREATE VIEW <schema>.<name>
AS SELECT * FROM public.<name>` over all public tables), so residue in one
DB's public repopulates the other's within one suite run (observed:
mnemo2db.public empty -> 40 tables mid-run). Only an at-rest sweep keeps
pgconn's ddl public-busy guard (lcm2-pg/pgconn.py:137) quiet.

What it does (idempotent):
  * drops the qa4-named residue schemas,
  * empties schema `public` in BOTH databases (vendor-era mnemosyne
    leftovers; agent data lives in agent schemas, never public — same
    classes scripts/sweep_public_residue.py removes on lcm2db),
  * drops the ad-hoc debug DBs,
  * leaves LIVE conftest throwaways (lcm2_test_*/mnemo2_test_*/shared_test_*)
    untouched — a concurrent suite's teardown owns those.
"""
import sys
import psycopg

BASE = "host=/home/mybuddy/lcm2-mnemo2-pg/pg/run port=54331 user=mnemo201 dbname="

SCHEMAS = [("mnemo2db", "mnemo2_qa4probe"), ("mnemo2db", "mnemo2_qa4glob"),
           ("mnemo2db", "mnemo2_qa4def"), ("lcm2db", "lcm2_qa4probe"),
           ("lcm2db", "lcm2_test_1"), ("lcm2db", "lcm2_r4test_probe")]
DBS = ("lcm2dbg", "lcm2dbg2", "zztestdb")


def main():
    for db, sch in SCHEMAS:
        with psycopg.connect(BASE + db, autocommit=True) as cx:
            cx.execute(f'DROP SCHEMA IF EXISTS "{sch}" CASCADE')
            print(f"dropped schema {db}.{sch}")
    for db in ("lcm2db", "mnemo2db"):
        with psycopg.connect(BASE + db, autocommit=True) as cx:
            rels = cx.execute(
                "select c.relname, c.relkind from pg_class c "
                "join pg_namespace n on n.oid = c.relnamespace "
                "where n.nspname = 'public' "
                "and c.relkind in ('r','p','m','v','f') order by 1").fetchall()
            for rel, kind in rels:
                stmt = "DROP VIEW" if kind in "vmf" else "DROP TABLE"
                cx.execute(f'{stmt} IF EXISTS public."{rel}" CASCADE')
            print(f"swept public in {db}: {len(rels)} relations")
    with psycopg.connect(BASE + "postgres", autocommit=True) as cx:
        for db in DBS:
            row = cx.execute(
                "select 1 from pg_database where datname=%s", (db,)).fetchone()
            if row:
                cx.execute('SELECT pg_terminate_backend(pid) FROM '
                           'pg_stat_activity WHERE datname=%s', (db,))
                cx.execute(f'DROP DATABASE "{db}"')
                print(f"dropped database {db}")
            else:
                print(f"database {db}: absent")
    # verify
    for db in ("lcm2db", "mnemo2db"):
        with psycopg.connect(BASE + db) as cx:
            n = cx.execute(
                "select count(*) from pg_class c join pg_namespace n "
                "on n.oid=c.relnamespace where n.nspname='public' "
                "and c.relkind in ('r','p','m','v','f')").fetchone()[0]
            print(f"verify {db}.public relations = {n}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
