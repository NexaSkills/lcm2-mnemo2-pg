"""SELFHEAL-SCHEMA (lesson mybuddy-clone, 2026-11-09) — mnemo side.

Same bug class as tests/lcm2/test_selfheal_schema_clone.py: mnemo-pg2's
Conn bootstraps unqualified CREATE TABLEs under search_path=mnemo2_<agent>
(the provider dispatches through _cx(ddl=True) per call). When that
schema was never authored by the setup scripts (fresh profile /
never-onboarded root surface), the first provider call died on 3F000 and
memory silently degraded.

Exercised through mnemo-pg2's OWN pgconn surface only (cross-suite bare
names are a documented combined-run minefield unrelated to this fix):
ddl=True Conn = the provider's per-call shape, ddl=False = a bare
checkout. Both must author the missing schema; a mid-life DROP must be
repaired by the next checkout in the SAME process (cache-free cure).
"""
import uuid

# bare module-level import: the mnemo2 conftest + _src_window hooks pin
# the bare keys to OUR files around every test in this dir (verified
# green standalone and in combined ordering; the by-path namespaced
# loader is a DIFFERENT instance and misses the Conn-layer cure).
import pgconn  # noqa: E402  (plugin module, via conftest sys.path)


def _fresh(dsn, prefix="mnemo2_test_sh_"):
    name = f"{prefix}{uuid.uuid4().hex[:8]}"
    with pgconn.connect(dsn, "public") as cx:
        cx.execute(f'DROP SCHEMA IF EXISTS "{name}" CASCADE')
    return name


def _exists(dsn, name):
    with pgconn.connect(dsn, "public") as cx:
        return bool(cx.query_scalar(
            "select exists(select 1 from pg_namespace where nspname=%s)",
            (name,)))


def _drop(dsn, name):
    with pgconn.connect(dsn, "public") as cx:
        cx.execute(f'DROP SCHEMA IF EXISTS "{name}" CASCADE')


def test_ddl_checkout_authors_schema_and_store(dsn):
    schema = _fresh(dsn)
    try:
        with pgconn.connect(dsn, schema, ddl=True):
            pass
        assert _exists(dsn, schema), "ddl checkout authored nothing"
        with pgconn.connect(dsn, schema, ddl=False) as cx:
            assert cx.query_scalar(
                "select exists(select 1 from pg_class c join pg_namespace n"
                " on n.oid=c.relnamespace"
                " where n.nspname=%s and c.relname='working_memory')",
                (schema,)), "working_memory missing after DDL bootstrap"
    finally:
        _drop(dsn, schema)


def test_plain_checkout_authors_missing_schema(dsn):
    schema = _fresh(dsn)
    try:
        with pgconn.connect(dsn, schema, ddl=False):
            pass
        assert _exists(dsn, schema), "plain checkout authors nothing"
    finally:
        _drop(dsn, schema)


def test_schema_drop_midlife_self_heals(dsn):
    schema = _fresh(dsn)
    try:
        with pgconn.connect(dsn, schema, ddl=True):
            pass
        _drop(dsn, schema)
        assert not _exists(dsn, schema)
        with pgconn.connect(dsn, schema, ddl=True):
            pass
        assert _exists(dsn, schema), "no self-heal after drop (cached skip?)"
    finally:
        _drop(dsn, schema)
    pgconn.shutdown_pools()
