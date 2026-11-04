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
    """Argument expression(s) passed to the pg_advisory_xact_lock call in
    pgconn.py's DDL bootstrap path. The SQL is built by string
    concatenation across execute(...) args, so scan the concatenated
    string literals of every Call for 'advisory_xact_lock' / 'advisory_lock'
    and take THAT call's trailing argument expressions."""
    for node in ast.walk(TREE):
        if not isinstance(node, ast.Call):
            continue
        # SUITEGREEN-MIKE-0810: the SQL literal may sit in ANY arg slot
        # (3f9349c form: execute("SELECT ...pg_advisory_xact_lock(...%s...
        # %s...)", (dsn, dbname)) — literal in args[0], key expressions in
        # the tuple at args[1]). Scanning only args[:1] for the literal and
        # then blindly returning args[1:] made the key-args join pick up
        # unrelated names (test1 RED on the current loader: joined contained
        # 'schema' from a different call's shape). Identify the SQL literal
        # arg by content, then take the key args = everything AFTER it.
        lit_idx = None
        for i, a in enumerate(node.args):
            for sub in ast.walk(a):
                if (isinstance(sub, ast.Constant) and isinstance(sub.value, str)
                        and "advisory" in sub.value
                        and "lock" in sub.value.lower()):
                    lit_idx = i
                    break
            if lit_idx is not None:
                break
        if lit_idx is None:
            continue
        return node.args[lit_idx + 1:]
    raise AssertionError("no pg_advisory_*_lock execute call found in pgconn.py")


def _param_names_bound_to_call(tree):
    """Map each execute(...) call node -> bound names of the enclosing
    function/method (params + self), so we can tell what dsn-only means."""
    out = {}
    for fn in ast.walk(tree):
        if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            names = {a.arg for a in fn.args.args} | {a.arg for a in fn.args.posonlyargs} \
                | {a.arg for a in fn.args.kwonlyargs}
            for node in ast.walk(fn):
                if isinstance(node, ast.Call):
                    out[node] = names
    return out


def test_ddl_lock_first_key_is_instance_wide():
    args = _boot_lock_arg_nodes()
    assert args, "advisory lock call must pass key(s)"
    src_first = ast.unparse(args[0])
    # The lock's key expression(s) must reference the DSN and never the
    # schema. Accept `(self.dsn,)`, `(self.dsn, ...)`, or a var bound to
    # self.dsn — but NOT f"{self.dsn}\x1f{self.schema}" style (the race).
    joined = " ".join(ast.unparse(a) for a in args)
    assert "dsn" in src_first.lower(), \
        f"advisory-lock key expression doesn't key on the dsn: {joined}"
    assert "schema" not in joined.lower(), \
        f"schema must not feature in the instance-wide lock key: {joined}"


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
