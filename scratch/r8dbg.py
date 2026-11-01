import sys, os, uuid
sys.path.insert(0, "plugins/lcm2-pg")
import pgconn, multiprocessing as mp
SOCKET = os.path.expanduser("~/lcm2-mnemo2-pg/pg/run")
DSN = f"dbname=lcm2db host={SOCKET} port=54331 user=mnemo201"
RUN = uuid.uuid4().hex[:12]

def w(args):
    schema, wid, n = args
    try:
        pgconn.shutdown_pools()
        with pgconn.connect(DSN, schema, set_session=True) as cx:
            v = cx.query("select current_schema(), setting from pg_settings where name='search_path'")
            for i in range(n):
                cx.execute("insert into conc_probe(tag,worker,seq,payload) values (%s,%s,%s,%s)", (RUN, wid, i, "x" * 200))
        return (schema, wid, v, None)
    except Exception as e:
        return (schema, wid, None, f"{type(e).__name__}: {e}")

SCHEMAS = ["lcm2_lcm2agent", "lcm2_mnemo2agent"]
for s in SCHEMAS:
    with pgconn.connect(DSN, "public", ddl=True) as cx:
        cx.execute(f'CREATE SCHEMA IF NOT EXISTS "{s}"')
    with pgconn.connect(DSN, s, ddl=True) as cx:
        cx.execute("CREATE TABLE IF NOT EXISTS conc_probe(tag text, worker int, seq int, payload text, primary key(tag,worker,seq))")
jobs = [(s, w, 3) for s in SCHEMAS for w in range(2)]
with mp.get_context("spawn").Pool(4) as p:
    for r in p.map(w, jobs):
        print(r)
with pgconn.connect(DSN, "public") as cx:
    for s in SCHEMAS:
        print(s, cx.query(f'select count(*) from "{s}".conc_probe where tag=%s', (RUN,))[0][0])
        cx.execute(f'delete from "{s}".conc_probe where tag=%s', (RUN,))
