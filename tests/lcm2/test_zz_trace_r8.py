"""Audit writes via fully-qualified public table from each worker."""
import multiprocessing as mp
import os
import uuid

import pgconn

SOCKET = os.path.expanduser("~/lcm2-mnemo2-pg/pg/run")
DSN = f"dbname=lcm2db host={SOCKET} port=54331 user=mnemo201"
RUN = os.environ.get("LCM2_R8_RUN") or uuid.uuid4().hex[:12]
SCHEMAS = ["lcm2_lcm2agent", "lcm2_mnemo2agent"]
WORKERS = 4
ROWS = 25


def _setup():
    with pgconn.connect(DSN, "public", ddl=True) as cx:
        cx.execute("CREATE TABLE IF NOT EXISTS zz_global_audit(run text,"
                   " schema text, worker int, seq int, ccs text,"
                   " at timestamptz default now())")
        cx.execute("delete from zz_global_audit where run=%s", (RUN,))
    for s in SCHEMAS:
        with pgconn.connect(DSN, "public", ddl=True) as cx:
            cx.execute(f'CREATE SCHEMA IF NOT EXISTS "{s}"')
        with pgconn.connect(DSN, s, ddl=True) as cx:
            cx.execute("CREATE TABLE IF NOT EXISTS conc_probe(tag text,"
                       " worker int, seq int, payload text,"
                       " primary key(tag, worker, seq))")


def _worker(args):
    schema, wid, n = args
    try:
        pgconn.shutdown_pools()
        with pgconn.connect(DSN, schema, set_session=True) as cx:
            for i in range(n):
                cx.execute("insert into conc_probe(tag,worker,seq,payload)"
                           " values (%s,%s,%s,%s)", (RUN, wid, i, "x" * 200))
                cx.execute("insert into public.zz_global_audit"
                           "(run,schema,worker,seq,ccs) "
                           "values (%s,%s,%s,%s,(select current_schema()))",
                           (RUN, schema, wid, i))
        return (schema, wid, n, None)
    except Exception as e:
        return (schema, wid, 0, f"{type(e).__name__}: {e}")


def test_trace_parallel_writes():
    print("RUN =", RUN)
    _setup()
    jobs = [(s, w, ROWS) for s in SCHEMAS for w in range(WORKERS)]
    with mp.get_context("spawn").Pool(8) as pool:
        results = pool.map(_worker, jobs)
    print("results:", results)
    with pgconn.connect(DSN, "public") as cx:
        rows = cx.query("select schema, ccs, count(*) from zz_global_audit"
                        " where run=%s group by 1,2 order by 1,2", (RUN,))
        print("GLOBAL AUDIT:", rows)
        for s in SCHEMAS:
            n = cx.query(f'select count(*) from "{s}".conc_probe where tag=%s',
                         (RUN,))[0][0]
            print("parent count", s, n)
        cx.execute("delete from zz_global_audit where run=%s", (RUN,))
    for s in SCHEMAS:
        with pgconn.connect(DSN, s, ddl=True) as cx:
            cx.execute("delete from conc_probe where tag=%s", (RUN,))
    pgconn.shutdown_pools()
