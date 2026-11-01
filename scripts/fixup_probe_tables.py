"""One-shot: make R8's _setup deterministic wrt existing conc_probe tables.

1) Remove public shadowing copies.
2) Rebuild the probe table in both agent schemas with the canonical shape, so
   no legacy table shape (e.g. missing primary key) can affect worker inserts.
Rows from other runs in-flight may be lost; the probe table is test-owned.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                "plugins", "lcm2-pg"))
import pgconn  # noqa: E402

DSN = ("dbname=lcm2db host=" + os.environ.get(
    "LCM2_PG_HOST", os.path.expanduser("~/lcm2-mnemo2-pg/pg/run"))
    + " port=54331 user=mnemo201")

with pgconn.connect(DSN, "public", ddl=True) as cx:
    for name in ("conc_probe", "conc_probe2"):
        n = cx.query_scalar(
            "select count(*) from pg_class c join pg_namespace n on n.oid=c.relnamespace "
            f"where n.nspname='public' and c.relname='{name}'")
        if n:
            cx.execute(f"DROP TABLE public.{name}")
            print("dropped public." + name)
    for s in ("lcm2_lcm2agent", "lcm2_mnemo2agent"):
        n = cx.query_scalar(
            "select count(*) from pg_class c join pg_namespace n on n.oid=c.relnamespace "
            f"where n.nspname='{s}' and c.relname='conc_probe'")
        cx.execute(f'DROP TABLE IF EXISTS "{s}".conc_probe')
        cx.execute(f'CREATE TABLE "{s}".conc_probe('
                   "tag text, worker int, seq int, payload text, "
                   "primary key(tag, worker, seq))")
        print(f"rebuilt {s}.conc_probe" + ("" if n else " (was missing)"))
print("done")
