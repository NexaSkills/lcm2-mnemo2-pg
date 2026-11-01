"""R4: parity with the vendor CREATE TABLE inventory (A4 wildcard rule).

The authoritative set is enumerated FROM the pinned vendor source with the
wildcard grep, NOT from A4's itemized prose. The plugin's bootstrap DDL must
cover every one of those names (in the agent's own schema).
"""
import pathlib
import re
import subprocess

import pytest

import pgconn  # from conftest sys.path

VENDOR_CANDIDATES = [
    pathlib.Path.home() / "lcm-upstream",
    pathlib.Path.home() / "lcm2-recon" / "lcm-upstream",
]

TABLE_RX = re.compile(
    r"CREATE\s+TABLE(\s+IF\s+NOT\s+EXISTS)?\s+[\"'`]?([a-zA-Z_0-9]+)", re.I)
VTABLE_RX = re.compile(
    r"CREATE\s+VIRTUAL\s+TABLE(\s+IF\s+NOT\s+EXISTS)?\s+[\"'`]?([a-zA-Z_0-9]+)", re.I)


def vendor_source():
    for c in VENDOR_CANDIDATES:
        if (c / "plugin.yaml").is_file():
            return c
    pytest.skip("vendor hermes-lcm checkout not found")


def vendor_tables(src, rx):
    out = set()
    for p in src.rglob("*.py"):
        if "tests" in p.parts or "__pycache__" in p.parts:
            continue
        for m in rx.finditer(p.read_text(errors="replace")):
            out.add(m.group(2).lower())
    return out


def test_wildcard_enumeration_matches_documented_count():
    src = vendor_source()
    tables = vendor_tables(src, TABLE_RX)
    # documented (this turn, vendor a13e709): 37 distinct names
    assert len(tables) == 37, f"vendor CREATE TABLE count drifted: {len(tables)}"


def test_virtual_tables_are_fts_only():
    src = vendor_source()
    vt = vendor_tables(src, VTABLE_RX)
    assert all(n.endswith("_fts") for n in vt), vt


def test_plugin_ddl_covers_every_vendor_table(dsn, schema):
    # bootstrap via the PLUGIN's own DDL (loaded from its file path so the
    # bare 'ddl' module name can never be shadowed by sibling suites)
    import importlib.util
    import pathlib
    import pgconn
    p = str(pathlib.Path(__file__).resolve().parents[2]
            / "plugins" / "lcm2-pg" / "ddl.py")
    spec = importlib.util.spec_from_file_location("_lcm2pg_ddl_parity", p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert hasattr(mod, "apply_schema") and len(mod.TABLES) == 37
    with pgconn.connect(dsn, schema, ddl=True) as cx:
        mod.apply_schema(cx)
        have = set(cx.query(
            "select table_name from information_schema.tables "
            "where table_schema = %s", (schema,)))
    missing = vendor_tables(vendor_source(), TABLE_RX) - have
    assert not missing, f"tables missing from lcm2-pg bootstrap: {sorted(missing)}"


def test_no_fts_virtual_tables_created(dsn, schema):
    # must go through the shipped code path: Conn.bootstrap()
    import pgconn
    with pgconn.connect(dsn, schema, ddl=True) as cx:
        cx.bootstrap()
        sql = "\n".join(cx.ddl_log)
    assert not VTABLE_RX.search(sql)
    # and the shipped bootstrap must be the real one (37 tables)
    assert "CREATE TABLE IF NOT EXISTS messages" in sql
