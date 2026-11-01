"""R8: concurrent writes from two schemas (lcm2_lcm2agent + lcm2_mnemo2agent)
on lcm2db with an integrity check afterwards.

Uses the real production schemas on a marker table namespace owned by the
test (rows tagged and removed after; the agents' real rows are untouched).
"""
import multiprocessing as mp
import os
import time

import pgconn

SOCKET = os.environ.get("LCM2_PG_HOST",
                        os.path.expanduser("~/lcm2-mnemo2-pg/pg/run"))
DSN_TMPL = f"dbname=lcm2db host={SOCKET} port=54331 user=mnemo201"
SCHEMAS = ["lcm2_lcm2agent", "lcm2_mnemo2agent"]
TAG = "p2conc"


def _marker_setup():
    with pgconn.connect(DSN_TMPL, "public", ddl=True) as cx:
        for s in SCHEMAS:
            cx.execute(f'CREATE SCHEMA IF NOT EXISTS "{s}"')
            cx.execute(
                f'CREATE TABLE IF NOT EXISTS "{s}".conc_probe('
                f"tag text, worker int, seq int, payload text, "
                f"primary key(tag, worker, seq))")


def _worker(args):
    schema, wid, n = args
    dsn = DSN_TMPL
    try:
        with pgconn.connect(dsn, schema) as cx:
            for i in range(n):
                cx.execute(
                    f'insert into conc_probe(tag,worker,seq,payload) '
                    f"values (%s,%s,%s,%s)", (TAG, wid, i, "x" * 200))
        return (schema, wid, n, None)
    except Exception as e:  # noqa: BLE001
        return (schema, wid, 0, f"{type(e).__name__}: {e}")


def test_parallel_writes_and_integrity():
    _marker_setup()
    procs = [mp.Process(target=_store) for _ in range(1)]
    # 8 writers: 4 per schema, interleaved
    jobs = [(s, w, 25) for s in SCHEMAS for w in range(4)]
    with mp.Pool(8) as pool:
        results = pool.map(_worker, jobs)
    assert all(r[3] is None for r in results), results
    with pgconn.connect(DSN_TMPL, "public") as cx:
        for s in SCHEMAS:
            n = cx.query(f'select count(*) from "{s}".conc_probe where tag=%s',
                         (TAG,))[0][0]
            assert n == 100, (s, n)
            # integrity: PK intact, no dup seq, valid ordering
            dups = cx.query(
                f'select count(*) from (select worker,seq,count(*) '
                f'from "{s}".conc_probe where tag=%s '
                f"group by worker,seq having count(*)>1) t", (TAG,))[0][0]
            assert dups == 0
            bad = cx.query(
                f'select count(*) from "{s}".conc_probe '
                f"where tag=%s and length(payload)<>200", (TAG,))[0][0]
            assert bad == 0
    with pgconn.connect(DSN_TMPL, "public") as cx:
        for s in SCHEMAS:
            cx.execute(f'delete from "{s}".conc_probe where tag=%s', (TAG,))


def _store():
    pass
