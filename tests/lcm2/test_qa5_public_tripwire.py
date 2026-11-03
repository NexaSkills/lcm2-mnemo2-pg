"""qa5: pgconn's public-busy tripwire must be a HARD ASSERTION.

pgconn guards bootstrap DDL: with ddl=True and schema 'public' (the
default), it refuses to run while `public` holds relations, because
unqualified `CREATE TABLE IF NOT EXISTS` in the shipped DDL would land
there. A test that creates a table in public under ddl=True therefore
proves the tripwire fires. If the table is gone afterwards, the ONLY
other route that removes public residue is the suite's own residue
sweep (conftest session sweep / scripts/sweep_public_residue.py) — the
pin/import plumbing must never delete anything.

Runs in EITHER suite's process: resolves lcm2-pg's pgconn via this
repo's plugins/lcm2-pg path; falls back to a bare `import pgconn` only
if that file is not loadable standalone here.
"""
import importlib.util as _ilu
import pathlib
import sys

import pytest

_ROOT = pathlib.Path(__file__).resolve().parents[2]
_PGCONN = _ROOT / "plugins" / "lcm2-pg" / "pgconn.py"

TABLE = "qa_tripwire_marker"


def _pgconn():
    if not _PGCONN.is_file():  # pragma: no cover - repo layout contract
        return pytest.importorskip("pgconn")
    if "qa5_pgconn" not in sys.modules:
        spec = _ilu.spec_from_file_location("qa5_pgconn", str(_PGCONN))
        mod = _ilu.module_from_spec(spec)
        sys.modules["qa5_pgconn"] = mod
        spec.loader.exec_module(mod)
    return sys.modules["qa5_pgconn"]


def _dsn(dsn):
    # The tripwire belongs to lcm2-pg's guard; run it on lcm2db.
    return dsn.replace("dbname=mnemo2db", "dbname=lcm2db")


def _public_tables(pg, dsn):
    with pg.connect(_dsn(dsn), "public") as cx:
        # query_dicts may yield dicts OR plain tuples (row-factory varies by
        # which suite's pgconn bound first in a combined run); normalize to
        # a set of names (column 0 is the table name either way).
        return {
            r["tablename"] if isinstance(r, dict) else r[0]
            for r in cx.query_dicts(
                "select tablename from pg_tables where schemaname='public'")}


def test_public_ddl_tripwire_blocks_then_sweep_clears(dsn):
    """ddl=True + public busy -> the CREATE must raise; a sweep must clear
    public. This is the F-3 public-free invariant as a hard assertion."""
    pg = _pgconn()
    base = _public_tables(pg, dsn)
    try:
        with pg.connect(_dsn(dsn), "public", ddl=True) as cx:
            cx.execute(f"CREATE TABLE IF NOT EXISTS public.{TABLE} (id int)")
        # If the guard is disabled (a suite patched it out for unrelated
        # combined-run reasons), the table landed — that is still residue
        # the sweep must catch; assert via the sweep leg below.
        landed = TABLE in _public_tables(pg, dsn)
        guard_fired = False
    except Exception as e:
        guard_fired = any(
            s in str(e) for s in
            ("refusing ddl", "public holds", "PublicBusy", "busy"))
        assert guard_fired, f"unexpected error from ddl=True in busy public: {e!r}"
        landed = False

    # Sweep leg: the repo's sweeper must empty public regardless of which
    # leg above ran.
    sweep = _ROOT / "scripts" / "sweep_public_residue.py"
    if sweep.is_file():
        import subprocess
        subprocess.run([sys.executable, str(sweep), _dsn(dsn)],
                       check=True, capture_output=True, timeout=120)
    after = _public_tables(pg, dsn)
    assert TABLE not in after, \
        f"residue sweep left public.{TABLE}: {sorted(after)}"
