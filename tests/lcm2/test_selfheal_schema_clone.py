"""SELFHEAL-SCHEMA (lesson mybuddy-clone, 2026-11-09).

A freshly named agent profile (or a root surface never run through
onboard_profile.sh) must self-heal its lcm2_<agent> schema at first
engine contact. Pre-fix, ONLY the setup scripts authored schemas: the
engine constructor's unqualified bootstrap CREATE TABLEs ran under
search_path=<missing schema>, Postgres answered 3F000
("no schema has been selected to create in"), the constructor died, and
the host silently fell back to the built-in compressor (mybuddy-clone
boot proof: 'Context engine lcm2-pg not found - falling back').

The cure lives in pgconn.Conn.__enter__, so it is exercised through the
pgconn surface ALONE (no engine import: cross-suite bare-name binding in
combined runs is a documented minefield unrelated to this fix). The two
legs below are exactly what LCM2Engine.__init__ does: a ddl=True Conn
plus cx.bootstrap() (engine.py 'with self._conn(ddl=True) as cx:
cx.bootstrap()'), and what the hook lane does: a ddl=False checkout.
"""
import importlib.util
import pathlib
import uuid

# suite accessor by FILE (same idiom as test_r4_tools_surface): the
# deterministic own-finder route; sys.path pinning is per-test, so a
# plain module import of a helper is not collection-safe.
_ACC_PY = pathlib.Path(__file__).resolve().parent / "_own.py"
_acc_spec = importlib.util.spec_from_file_location("_suite_own_heal", _ACC_PY)
_acc = importlib.util.module_from_spec(_acc_spec)
_acc_spec.loader.exec_module(_acc)


def _pgconn():
    return _acc.own("pgconn")


def _fresh_schema(dsn, prefix="lcm2_test_sh_"):
    name = f"{prefix}{uuid.uuid4().hex[:8]}"
    with _pgconn().connect(dsn, "public") as cx:
        cx.execute(f'DROP SCHEMA IF EXISTS "{name}" CASCADE')
        assert cx.query_scalar(
            "select exists(select 1 from pg_namespace where nspname=%s)",
            (name,)) is False
    return name


def _exists(dsn, name):
    with _pgconn().connect(dsn, "public") as cx:
        return bool(cx.query_scalar(
            "select exists(select 1 from pg_namespace where nspname=%s)",
            (name,)))


def _drop(dsn, name):
    with _pgconn().connect(dsn, "public") as cx:
        cx.execute(f'DROP SCHEMA IF EXISTS "{name}" CASCADE')


def test_bootstrap_from_zero_authors_schema_and_store(dsn):
    """Exactly the LCM2Engine.__init__ shape on a schema that does not
    exist: ddl=True Conn + bootstrap(). Must not raise, must leave the
    store tables (pre-fix: 3F000 killed the constructor)."""
    schema = _fresh_schema(dsn)
    try:
        with _pgconn().connect(dsn, schema, ddl=True) as cx:
            cx.bootstrap()
            assert cx.query_scalar(
                "select exists(select 1 from pg_class c join pg_namespace n"
                " on n.oid=c.relnamespace"
                " where n.nspname=%s and c.relname='messages')",
                (schema,)), "messages table missing after bootstrap"
    finally:
        _drop(dsn, schema)


def test_plain_checkout_authors_missing_schema(dsn):
    """The hook lane (ddl=False first contact on a fallback boot) must
    author the schema too."""
    schema = _fresh_schema(dsn)
    try:
        with _pgconn().connect(dsn, schema, ddl=False):
            pass
        assert _exists(dsn, schema), (
            "plain connect left no schema: new-agent self-heal still "
            "depends on the setup scripts (the mybuddy-clone bug class)")
    finally:
        _drop(dsn, schema)


def test_schema_drop_midlife_self_heals(dsn):
    """Cache-free self-heal: an operator DROP of the agent schema is
    repaired by the very next connection in the SAME process."""
    schema = _fresh_schema(dsn)
    try:
        with _pgconn().connect(dsn, schema, ddl=True) as cx:
            cx.bootstrap()
        _drop(dsn, schema)
        assert not _exists(dsn, schema)
        with _pgconn().connect(dsn, schema, ddl=False):
            pass
        assert _exists(dsn, schema), "no self-heal after drop (cached skip?)"
    finally:
        _drop(dsn, schema)
    _pgconn().shutdown_pools()
