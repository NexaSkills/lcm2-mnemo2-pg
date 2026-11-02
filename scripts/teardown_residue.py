#!/usr/bin/env python3
"""Teardown + persistent public sweep for Phase A fix6 (token LCM2-MNEMO2-PHASEA-FIX6).

Persistent-state half of the N-3 fix: drop the qa4-named residue schemas and
empty schema `public` in BOTH databases (they are vendor-era leftovers; agent
data lives in agent schemas, never public — same classes scripts/
sweep_public_residue.py already removes on lcm2db).
"""
import sys
import psycopg

BASE = "host=/home/mybuddy/lcm2-mnemo2-pg/pg/run port=54331 user=mnemo201 dbname="

SCHEMAS = [("mnemo2db", "mnemo2_qa4probe"), ("mnemo2db", "mnemo2_qa4glob"),
           ("mnemo2db", "mnemo2_qa4def"), ("lcm2db", "lcm2_qa4probe"),
           ("lcm2db", "lcm2_test_1"), ("lcm2db", "lcm2_r4test_probe")]
DBS = [("postgres", d) for d in ("lcm2dbg", "lcm2dbg2", "zztestdb")]
# NOTE: mnemo2_test_* / shared_test_* in mnemo2db are LIVE right now in
# concurrent suite runs — their conftest sweeps own them; untouched here.


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
                stmt = ("DROP VIEW" if kind in "vmf" else "DROP TABLE") \
                    if kind != "p" else "DROP TABLE"
                cx.execute(f'{stmt} IF EXISTS public."{rel}" CASCADE')
            print(f"swept public in {db}: {len(rels)} relations")
    with psycopg.connect(BASE + "postgres", autocommit=True) as cx:
        for _, db in DBS:
            row = cx.execute(
                "select 1 from pg_database where datname=%s", (db,)).fetchone()
            if row:
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
