"""R8: concurrent writes from two schemas (lcm2_lcm2agent + lcm2_mnemo2agent)
on lcm2db with an integrity check afterwards.

Uses the real production schemas on a marker table namespace owned by the
test (rows tagged with a unique run-id and removed after; the agents' real
rows are untouched).
"""
import multiprocessing as mp
import multiprocessing.context
import os
import uuid

import pgconn

SOCKET = os.environ.get("LCM2_PG_HOST",
                        os.path.expanduser("~/lcm2-mnemo2-pg/pg/run"))
DSN = f"dbname=lcm2db host={SOCKET} port=54331 user=mnemo201"
SCHEMAS = ["lcm2_lcm2agent", "lcm2_mnemo2agent"]
RUN = os.environ.get("LCM2_R8_RUN") or uuid.uuid4().hex[:12]  # unique per run
WORKERS = 4
ROWS = 25


def _setup():
    for s in SCHEMAS:
        with pgconn.connect(DSN, "public", ddl=True) as cx:
            cx.execute(f'CREATE SCHEMA IF NOT EXISTS "{s}"')
        with pgconn.connect(DSN, s, ddl=True) as cx:
            cx.execute(
                "CREATE TABLE IF NOT EXISTS conc_probe("
                "tag text, worker int, seq int, payload text, "
                "primary key(tag, worker, seq))")
    # unqualified DDL follows search_path (schema, public): if public
    # already holds a conc_probe (e.g. created while public was the only
    # path entry), the CREATE above resolves there and workers would
    # silently write to the wrong table. Same DDL, schema-qualified into
    # each agent schema — idempotent, never touches public.
    for s in SCHEMAS:
        with pgconn.connect(DSN, "public", ddl=True) as cx:
            cx.execute(
                f'CREATE TABLE IF NOT EXISTS "{s}".conc_probe('
                "tag text, worker int, seq int, payload text, "
                "primary key(tag, worker, seq))")
    # fail loudly rather than silently mixing rows across schemas.
    # Cleanup first: a stray public.conc_probe left by an older run (before
    # this test created the table schema-qualified) would otherwise wedge
    # every later run. The qualified CREATE above guarantees the real
    # tables exist in the agent schemas regardless.
    with pgconn.connect(DSN, "public", ddl=True) as cx:
        stray = cx.query_scalar(
            "select count(*) from pg_class c "
            "join pg_namespace n on n.oid = c.relnamespace "
            "where n.nspname = 'public' and c.relname = 'conc_probe'")
        if stray:
            cx.execute("DROP TABLE IF EXISTS public.conc_probe")


def _worker(args):
    schema, wid, n = args
    try:
        # fresh connections in this worker only: never reuse a pool object
        # inherited across fork() (its sockets belong to the parent).
        # set_session=True pins search_path via session settings so the
        # per-connection SET is re-applied on every pool reconnect too —
        # a plain SET after pool checkout can be lost silently.
        pgconn.shutdown_pools()
        with pgconn.connect(DSN, schema, set_session=True) as cx:
            for i in range(n):
                cx.execute(
                    "insert into conc_probe(tag,worker,seq,payload) "
                    "values (%s,%s,%s,%s)", (RUN, wid, i, "x" * 200))
        return (schema, wid, n, None)
    except Exception as e:  # noqa: BLE001
        return (schema, wid, 0, f"{type(e).__name__}: {e}")


def test_parallel_writes_and_integrity():
    _setup()
    jobs = [(s, w, ROWS) for s in SCHEMAS for w in range(WORKERS)]
    with mp.get_context("spawn").Pool(8) as pool:
        results = pool.map(_worker, jobs)
    assert all(r[3] is None for r in results), results
    with pgconn.connect(DSN, "public") as cx:
        for s in SCHEMAS:
            n = cx.query(f'select count(*) from "{s}".conc_probe where tag=%s',
                         (RUN,))[0][0]
            assert n == WORKERS * ROWS, (s, n)
            dups = cx.query(
                f'select count(*) from (select worker,seq,count(*) '
                f'from "{s}".conc_probe where tag=%s '
                f"group by worker,seq having count(*)>1) t", (RUN,))[0][0]
            assert dups == 0
            bad = cx.query(
                f'select count(*) from "{s}".conc_probe '
                f"where tag=%s and length(payload)<>200", (RUN,))[0][0]
            assert bad == 0
    with pgconn.connect(DSN, "public") as cx:
        for s in SCHEMAS:
            cx.execute(f'delete from "{s}".conc_probe where tag=%s', (RUN,))
