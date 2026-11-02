"""F3 (QA phase2): public schema must stay table-free.

Invisible-write shadowing is the exact failure mode behind the R8 flake:
search_path '<schema>, public' lets any surviving public relation shadow
agent-schema writes. scripts/sweep_public_residue.py removes the residue;
this test asserts the invariant end-state (and that extensions were moved
to pg_catalog so a public-free path still resolves vector/pg_trgm types).
DB-gated like the rest of the suite: skipped without a reachable instance.
"""
import os
import subprocess
import shutil

import pytest

DSN = os.environ.get("LCM2_PG_DSN") or (
    "postgresql:///lcm2db?host=%s&port=%s&user=mnemo201"
    % (os.path.expanduser("~/lcm2-mnemo2-pg/pg/run"),
       os.environ.get("LCM2_PG_PORT", "54331")))


def _psql(sql):
    exe = shutil.which("psql")
    if not exe:
        pytest.skip("psql not on PATH")
    r = subprocess.run([exe, DSN, "-At", "-v", "ON_ERROR_STOP=1", "-c", sql],
                       capture_output=True, text=True)
    if r.returncode != 0:
        pytest.skip(f"lcm2db instance unreachable: {r.stderr.strip()[:120]}")
    return r.stdout.strip()


def test_public_has_no_relations():
    n = int(_psql(
        "select count(*) from pg_class c "
        "join pg_namespace n on n.oid = c.relnamespace "
        "where n.nspname = 'public' "
        "and c.relkind in ('r','p','m','v','f')"))
    assert n == 0, f"public holds {n} relation(s); run scripts/sweep_public_residue.py"


def test_no_probe_residue_schemas():
    import re
    # Broadened (qa-r4 F-3 sweep class): debug sessions leave residue schemas
    # under several naming conventions — probe_*/lcm2_probe* (probe runs),
    # lcm2_test_* (conftest throwaway schemas that survived a crashed run),
    # lcm2_dbg_* (ad-hoc debug-session uuid schemas, e.g. lcm2_dbg_f86854e7c9
    # left by F1 debug). Catch all classes generically so no residue family
    # slips past the sweep. qa4 classes added (gate-probe residue
    # named *_qa4* and cross-DB throwaway families from the sibling suite).
    bad = [s for s in _psql("select nspname from pg_namespace").splitlines()
           if re.match(r"^(probe_.*|lcm2_probe.*|lcm2_dbg_.*|lcm2_test_.*|lcm2_qa4.*|mnemo2_qa4.*|mnemo2_test_.*|shared_test_.*)$", s)]
    assert bad == [], f"probe residue schemas remain: {bad}"


def test_extensions_resolve_without_public():
    # search_path without public still resolves extension types: extensions
    # live in pg_catalog (implicitly on every search_path) — pgconn's
    # _ensure_ext_in_pg_catalog guarantees this at first connect.
    _psql("set search_path = public")  # no-op sanity
    ext = _psql(
        "select e.extname || '=' || n.nspname from pg_extension e "
        "join pg_namespace n on n.oid = e.extnamespace "
        "where e.extname in ('vector','pg_trgm') order by 1")
    for line in ext.splitlines():
        name, nsp = line.split("=")
        assert nsp == "pg_catalog", (
            f"extension {name} lives in {nsp}; public-free search_path "
            "would fail to resolve its types")
