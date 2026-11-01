import os, uuid, multiprocessing as mp, sys
sys.path.insert(0, os.path.dirname(__file__))
os.environ.setdefault("LCM2_PG_DSN", "dbname=lcm2db host=" + os.path.expanduser("~/lcm2-mnemo2-pg/pg/run") + " port=54331 user=mnemo201")
from r8worker import w, DSN
import pgconn

RUN = uuid.uuid4().hex[:12]
SCHEMAS = ["lcm2_lcm2agent", "lcm2_mnemo2agent"]
for s in SCHEMAS:
    with pgconn.connect(DSN, "public", ddl=True) as cx:
        cx.execute(f'CREATE SCHEMA IF NOT EXISTS "{s}"')
    with pgconn.connect(DSN, s, ddl=True) as cx:
        cx.execute("CREATE TABLE IF NOT EXISTS conc_probe(tag text, worker int, seq int, payload text, primary key(tag,worker,seq))")
jobs = [(RUN, s, w, 5) for s in SCHEMAS for w in range(3)]
with mp.get_context("spawn").Pool(6) as p:
    for r in p.map(w, jobs):
        print(r)
with pgconn.connect(DSN, "public") as cx:
    for s in SCHEMAS:
        print("count", s, cx.query(f'select count(*) from "{s}".conc_probe where tag=%s', (RUN,))[0][0])
        cx.execute(f'delete from "{s}".conc_probe where tag=%s', (RUN,))
