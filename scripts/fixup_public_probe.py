"""Remove public.conc_probe if an older run left it (shadowing fixup).

The R8 test itself now drops this table in _setup; this one-off exists
because the failing runs' own _setup predated that guard.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                "plugins", "lcm2-pg"))
import pgconn  # noqa: E402

with pgconn.connect(pgconn.default_dsn(), "public", ddl=True) as cx:
    n = cx.query_scalar(
        "select count(*) from pg_class c "
        "join pg_namespace n on n.oid = c.relnamespace "
        "where n.nspname = 'public' and c.relname = 'conc_probe'")
    print("public.conc_probe exists:", n)
    if n:
        cx.execute("DROP TABLE public.conc_probe")
        print("dropped")
