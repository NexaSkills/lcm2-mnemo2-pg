"""Server log: full statement trace of the R8 run (who touches conc_probe)."""
import multiprocessing as mp
import os
import subprocess
import sys
import pathlib
import time
import uuid

sys.path.insert(0, str(pathlib.Path.home() / "lcm2-mnemo2-pg" / "plugins" / "lcm2-pg"))
import pgconn

PSQL = str(pathlib.Path.home() / "lcm-pg" / "pg" / "bin" / "psql")
HOST = os.environ.get("LCM2_PG_HOST") or str(
    pathlib.Path.home() / "lcm2-mnemo2-pg" / "pg" / "run")  # fix7c split
PORT = os.environ.get("LCM2_PG_PORT", "54331")  # fix7c: instance split (env bind)
DSN = f"dbname=lcm2db host={HOST} port={PORT} user=mnemo201"
RUN = os.environ.get("LCM2_R8_RUN") or uuid.uuid4().hex[:12]
SCHEMAS = ["lcm2_lcm2agent", "lcm2_mnemo2agent"]


def psql(sql):
    r = subprocess.run([PSQL, "-h", HOST, "-p", PORT, "-U", "mnemo201",
                        "-d", "lcm2db", "-Atc", sql], capture_output=True, text=True)
    return r.stdout.strip() or r.stderr.strip()


def _setup():
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
        return (schema, wid, n, None)
    except Exception as e:
        return (schema, wid, 0, f"{type(e).__name__}: {e}")


def test_srvlog_run():
    print("RUN =", RUN)
    psql("alter system set log_statement='all'")
    psql("select pg_reload_conf()")
    time.sleep(0.5)
    mark = psql("select pg_current_wal_lsn()")
    _setup()
    jobs = [(s, w, 5) for s in SCHEMAS for w in range(2)]
    with mp.get_context("spawn").Pool(4) as pool:
        results = pool.map(_worker, jobs)
    print("results:", results)
    with pgconn.connect(DSN, "public") as cx:
        for s in SCHEMAS:
            n = cx.query(f'select count(*) from "{s}".conc_probe where tag=%s',
                         (RUN,))[0][0]
            print("parent count", s, n)
    pgconn.shutdown_pools()
    time.sleep(0.5)
    import glob
    # cluster log dir: 'log' (hand-baked reference cluster) OR 'pg_log'
    # (setup_instance.sh default landing) — fleet layout difference,
    # 2026-11-09 host-suite hunt.
    cands = []
    for d in ("log", "pg_log"):
        cands += glob.glob(os.path.expanduser(f"~/lcm2-mnemo2-pg/pg/data/{d}/*.log"))
    log = sorted(cands, key=os.path.getmtime)[-1]
    txt = open(log, errors="replace").read()
    lines = [l for l in txt.splitlines()
             if RUN in l and ("conc_probe" in l or "search_path" in l)]
    print(f"--- {len(lines)} lines mention tag/SET on conc_probe connections")
    # group by pid: show first 3 + inserts count per pid
    from collections import defaultdict
    per = defaultdict(list)
    for l in lines:
        pid = l.split("[")[1].split("]")[0] if "[" in l else "?"
        per[pid].append(l)
    for pid, ls in list(per.items())[:6]:
        ins = [l for l in ls if "insert into conc_probe" in l]
        sets = [l for l in ls if "set_config" in l]
        print(f"pid {pid}: {len(ls)} lines, {len(ins)} inserts, {len(sets)} set_config")
        for l in ls[:4]:
            print("   ", l.split("LOG:")[-1].strip()[:140])
    psql("delete from conc_probe where tag=%s" % ("' + RUN + '")) if False else None
    psql(f"alter system reset log_statement")
    psql("select pg_reload_conf()")
    for s in SCHEMAS:
        with pgconn.connect(DSN, s, ddl=True) as cx:
            cx.execute("delete from conc_probe where tag=%s", (RUN,))
    pgconn.shutdown_pools()
