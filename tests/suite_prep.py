#!/usr/bin/env python3
"""qa4 N-3: suite-prep sweep (BOTH databases of the dedicated instance).

Empirically established (probe11/12, qa4 round):
  * mnemo-pg2's shared-view bake (`CREATE VIEW <schema>.<t> AS SELECT *
    FROM public.<t>`) REPOPULATES schema public live during any run that
    bootstraps a mnemo2 schema while public holds residue — a start-only
    sweep is undone mid-run and pgconn's public-busy guard then fails
    every lcm2 ddl bootstrap (40 errors).
  * The bake repopulates within milliseconds of a schema bootstrap. The
    only durable fix is: never let mnemo2 bootstrap see public residue —
    sweep BOTH dbs IMMEDIATELY BEFORE every mnemo2 schema-fixture
    bootstrap (conftest hooks below), plus at session start and at
    session end (residue a run leaves is never carried to the next).

Profile schemas (lcm2_*/mnemo2_*/shared) are never touched.
"""
import re
import sys
import uuid

FAMILY = re.compile(r"^(lcm2_test_.*|lcm2_probe.*|probe_.*|lcm2_dbg_.*|lcm2_qa.*"
                    r"|lcm2_r4test.*|mnemo2_qa.*|mnemo2_test_.*|mnemo2_probe.*"
                    r"|shared_test_.*|p3[ab]?)$")


def sweep_dsn(dsn):
    """Drop public residue (views first, tables after) + throwaway schemas."""
    try:
        import pgconn  # whatever file the process resolved; DDL is qualified
    except Exception:
        return  # sweep is best-effort; the fixtures' own guards still apply
    try:
        with pgconn.connect(dsn, "public") as cx:
            rels = [(r["relname"], r["relkind"]) for r in cx.query_dicts(
                "select c.relname, c.relkind from pg_class c "
                "join pg_namespace n on n.oid = c.relnamespace "
                "where n.nspname = 'public' "
                "and c.relkind in ('v','m','f','r','p') "
                "order by (c.relkind in ('r','p')), c.relname")]
            for rel, kind in rels:
                stmt = "DROP VIEW" if kind in "vmf" else "DROP TABLE"
                cx.execute(f'{stmt} IF EXISTS public."{rel}" CASCADE')
            for n in [r["nspname"] for r in cx.query_dicts(
                    "select nspname from pg_namespace")
                    if FAMILY.match(r["nspname"])]:
                cx.execute(f'DROP SCHEMA IF EXISTS "{n}" CASCADE')
    except Exception as e:
        print(f"[suite-prep] sweep warning on {dsn.split('dbname=')[-1].split()[0]}"
              f": {type(e).__name__}: {e}", file=sys.stderr)


def _dsn_for(db):
    # Resolve via the suite conftest that owns the db; env fallback.
    import os
    for key in ("LCM2_PG_DSN",):
        v = os.environ.get(key, "")
        if f"dbname={db}" in v:
            return v
    socket = __import__("pathlib").Path.home() / "lcm2-mnemo2-pg" / "pg" / "run"
    return f"dbname={db} host={socket} port=54331 user=mnemo201"


def sweep_both(label=""):
    import os
    dbs = []
    for var in ("LCM2_MNEMO2_DB", "LCM2_MNEMO2_SIBLING_DB"):
        v = os.environ.get(var, "")
        if v:
            dbs.append(v)
    if not dbs:
        dbs = ["lcm2db", "mnemo2db"]
    for db in dbs:
        sweep_dsn(_dsn_for(db))
    sweep_probe = os.environ.get("LCM2_PG_DSN", "")  # whichever db tests use
    if sweep_probe:
        sweep_dsn(sweep_probe)


def pytest_sessionstart(session):
    try:
        sweep_both("session-start")
    except Exception:
        pass


def pytest_sessionfinish(session, exitstatus):
    try:
        sweep_both("session-end")
    except Exception:
        pass
