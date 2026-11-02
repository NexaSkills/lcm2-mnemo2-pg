#!/usr/bin/env python3
# Gate teardown census (round-5 close, token LCM2-MNEMO2-PHASEA-FIX5):
# drop the gate-probe residue schemas qa3+qa4 named, on :54331, then
# print the census. Same idempotent shape as scripts/teardown_residue.py.
import psycopg

BASE = "host=/home/mybuddy/lcm2-mnemo2-pg/pg/run port=54331 user=mnemo201 dbname="
SCHEMAS = [("lcm2db", "lcm2_qa3probe"), ("mnemo2db", "mnemo2_qa3probe"),
           ("mnemo2db", "mnemo2_qa3direct_home"),
           ("mnemo2db", "mnemo2_qa4probe"), ("mnemo2db", "mnemo2_qa4glob"),
           ("mnemo2db", "mnemo2_qa4def"), ("lcm2db", "lcm2_qa4probe"),
           ("lcm2db", "lcm2_r4test_probe")]
DBS = ["lcm2dbg", "lcm2dbg2", "zztestdb"]

for db, sch in SCHEMAS:
    with psycopg.connect(BASE + db, autocommit=True) as cx:
        had = cx.execute("select 1 from pg_namespace where nspname=%s", (sch,)).fetchone()
        cx.execute(f'DROP SCHEMA IF EXISTS "{sch}" CASCADE')
        print(f"{'dropped' if had else 'absent '} schema {db}.{sch}")
with psycopg.connect(BASE + "postgres", autocommit=True) as cx:
    for db in DBS:
        had = cx.execute("select 1 from pg_database where datname=%s", (db,)).fetchone()
        cx.execute(f'DROP DATABASE IF EXISTS "{db}"')
        print(f"{'dropped' if had else 'absent '} database {db}")

print("--- CENSUS ---")
for db in ("postgres", "lcm2db", "mnemo2db"):
    with psycopg.connect(BASE + db, autocommit=True) as cx:
        rows = cx.execute(
            "select nspname from pg_namespace where nspname not in "
            "('pg_catalog','information_schema','pg_toast','public') "
            "and (nspname like '%qa3%' or nspname like '%qa4%' "
            " or nspname like '%probe%' or nspname like '%_test_%') "
            "order by 1").fetchall()
        print(f"{db}: residue={[r[0] for r in rows] or 'NONE'}")
with psycopg.connect(BASE + "postgres", autocommit=True) as cx:
    dbs = [r[0] for r in cx.execute(
        "select datname from pg_database where datname not like 'template%' "
        "order by 1").fetchall()]
    print(f"databases={dbs}")
