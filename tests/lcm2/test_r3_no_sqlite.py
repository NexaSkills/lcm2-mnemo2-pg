"""R3: zero sqlite in shipped lcm2-pg code paths (static half).

QA tests the runtime half too (forced PG failure must show no sqlite
fallback); this file pins the static invariants.
"""
import pathlib
import re
import subprocess

import pytest

PLUGIN = pathlib.Path(__file__).resolve().parents[2] / "plugins" / "lcm2-pg"


def _py_files():
    return [p for p in PLUGIN.rglob("*.py") if "__pycache__" not in str(p)]


def test_plugin_dir_exists():
    assert PLUGIN.is_dir()
    assert (PLUGIN / "plugin.yaml").is_file()


@pytest.mark.parametrize("pattern", [
    r"\bsqlite3\b",
    r"\bsqlite_vec\b",
    r"\bvec0\b",
    r"\bfts5\b",
    r"\bPRAGMA\b",
    r"sqlite_master",
    r"CREATE\s+VIRTUAL\s+TABLE",
])
def test_no_sqlite_mechanisms(pattern):
    rx = re.compile(pattern)
    hits = [f"{p}:{i+1}: {ln.strip()}"
            for p in _py_files()
            for i, ln in enumerate(p.read_text().splitlines())
            if rx.search(ln)]
    assert not hits, f"sqlite mechanism {pattern!r} in shipped code:\n" + "\n".join(hits)


def test_import_sqlite3_never_used():
    # importlib trickery or sqlite in strings: catch any 'sqlite' token at all
    offenders = [str(p) for p in _py_files() if "sqlite" in p.read_text().lower()]
    assert not offenders, f"'sqlite' token present in: {offenders}"


def test_runtime_no_sqlite_module_load(monkeypatch):
    """Importing the plugin must not drag sqlite3 into loaded modules."""
    import sys
    before = set(sys.modules)
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "lcm2_pg_probe", PLUGIN / "__init__.py",
        submodule_search_locations=[str(PLUGIN)])
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    new = set(sys.modules) - before
    assert not [m for m in new if m.startswith("sqlite")], \
        f"plugin import loaded sqlite modules: {sorted(new)}"
