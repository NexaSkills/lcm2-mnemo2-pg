"""R8: concurrent writes from two schemas (lcm2_lcm2agent + lcm2_mnemo2agent)
on lcm2db with an integrity check afterwards.

Uses the real production schemas on a marker table namespace owned by the
test (rows tagged with a unique run-id and removed after; the agents' real
rows are untouched).

RUN-tag determinism (R8 flake root cause, 2026-11-01): the parent resolves
RUN once (env LCM2_R8_RUN or a fresh uuid). mp "spawn" children re-import
this module, so the module-level uuid fallback re-fires PER WORKER — under
bare env each worker inserts under its OWN tag and the parent's
count(tag=RUN) sees 0 (the historical "orphan-reaper race" failure: writes
landed fine, just under 8 scattered tags). The parent's RUN is carried to
every worker inside the job tuple and re-pinned there, so env-unset runs are
deterministic and concurrent suites with distinct fallback tags no longer
collide.
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
RUN = os.environ.get("LCM2_R8_RUN") or uuid.uuid4().hex[:12]  # per-process
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
    for s in SCHEMAS:
        with pgconn.connect(DSN, "public", ddl=True) as cx:
            cx.execute(
                f'CREATE TABLE IF NOT EXISTS "{s}".conc_probe('
                "tag text, worker int, seq int, payload text, "
                "primary key(tag, worker, seq))")
    with pgconn.connect(DSN, "public", ddl=True) as cx:
        stray = cx.query_scalar(
            "select count(*) from pg_class c "
            "join pg_namespace n on n.oid = c.relnamespace "
            "where n.nspname = 'public' and c.relname = 'conc_probe'")
        if stray:
            cx.execute("DROP TABLE IF EXISTS public.conc_probe")


def _worker(args):
    schema, wid, n, run = args
    global RUN
    RUN = run  # pin the parent's tag (spawn children don't inherit globals)
    try:
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
    jobs = [(s, w, ROWS, RUN) for s in SCHEMAS for w in range(WORKERS)]
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
