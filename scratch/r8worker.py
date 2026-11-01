import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "plugins", "lcm2-pg"))
import pgconn

SOCKET = os.path.expanduser("~/lcm2-mnemo2-pg/pg/run")
DSN = f"dbname=lcm2db host={SOCKET} port=54331 user=mnemo201"


def w(args):
    run, schema, wid, n = args
    try:
        pgconn.shutdown_pools()
        with pgconn.connect(DSN, schema, set_session=True) as cx:
            cx.execute("insert into conc_probe(tag,worker,seq,payload) values (%s,%s,%s,%s)", (run, wid, 0, "x" * 200))
            v = cx.query("select current_database(), current_user, current_schema(), setting from pg_settings where name='search_path'")
            for i in range(1, n):
                cx.execute("insert into conc_probe(tag,worker,seq,payload) values (%s,%s,%s,%s)", (run, wid, i, "x" * 200))
            cnt = cx.query("select count(*) from conc_probe where tag=%s", (run,))
        return (schema, wid, v, cnt, None)
    except Exception as e:
        return (schema, wid, None, None, f"{type(e).__name__}: {e}")
