"""R8 — DDL bootstrap must serialize at INSTANCE level, not per-schema.

pg_type/pg_depend/pg_extension are DATABASE-GLOBAL catalogs: two
backends running CREATE TABLE IF NOT EXISTS in DIFFERENT schemas pass
each other's existence check and the loser gets
pg_type_typname_nsp_index UniqueViolation instead of the skip. The
bootstrap lock's FIRST key must therefore be the SAME for every schema
of one instance (dsn-keyed, not schema-keyed). Regression guard for the
observed failure class.
"""
import ast
import pathlib
import uuid

import pgconn

SRC = pathlib.Path(pgconn.__file__).read_text()
TREE = ast.parse(SRC)


def _boot_lock_arg_nodes():
    """Find the argument expression(s) passed to the pg_advisory_xact_lock
    / pg_advisory_lock call inside pgconn.py's DDL bootstrap path."""
    for node in ast.walk(TREE):
        if isinstance(node, ast.Call):
            f = node.func
            name = f.attr if isinstance(f, ast.Attribute) else \
                (f.id if isinstance(f, ast.Name) else "")
            if "advisory" in str(getattr(f, "attr", getattr(f, "id", ""))):
                return node.args
    raise AssertionError("no pg_advisory_*_lock call found in pgconn.py")


def test_ddl_lock_first_key_is_instance_wide():
    args = _boot_lock_arg_nodes()
    assert args, "advisory lock call must pass key(s)"
    first = ast.dump(args[0])
    # The first key must be derived from the DSN alone — never from
    # self.schema or an f-string mixing dsn+schema (the race that made
    # two schemas bootstrap concurrently).
    assert "self.dsn" in first or getattr(args[0], "attr", None) == "dsn", \
        f"advisory-lock first key not dsn-only: {ast.unparse(args[0])}"
    src_first = ast.unparse(args[0])
    assert "schema" not in src_first.lower(), \
        f"schema must not feature in the instance-wide lock key: {src_first}"


def test_concurrent_different_schema_bootstraps_no_unique_violation(dsn):
    """Two fresh schemas, two threads, first-write DDL at once: neither
    may surface a UniqueViolation (they must serialize on pg_type)."""
    import threading
    names = ["mnemo2_test_ddlrace_" + uuid.uuid4().hex[:8] for _ in range(2)]
    errors: list = []
    barrier = threading.Barrier(2)

    def boot(name):
        try:
            with pgconn.connect(dsn, "public", ddl=True) as cx:
                cx.execute(f'CREATE SCHEMA "{name}"')
            barrier.wait(30)  # both hit first-write DDL together
            with pgconn.connect(dsn, name, ddl=True) as cx:
                cx.execute("SELECT 1")
        except Exception as e:  # noqa: BLE001
            errors.append(repr(e))

    ts = [threading.Thread(target=boot, args=(n,)) for n in names]
    for t in ts:
        t.start()
    for t in ts:
        t.join(60)
    try:
        assert not any("UniqueViolation" in e for e in errors), errors
        assert not errors, errors
        for n in names:
            with pgconn.connect(dsn, n) as cx:
                assert cx.query_scalar(
                    "select count(*) from working_memory") == 0
    finally:
        with pgconn.connect(dsn, "public", ddl=True) as cx:
            for n in names:
                cx.execute(f'DROP SCHEMA IF EXISTS "{n}" CASCADE')
