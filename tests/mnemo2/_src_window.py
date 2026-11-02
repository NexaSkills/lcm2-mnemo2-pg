"""Combined-run source-introspection window (fix6 N-3 lane hygiene).

A combined pytest process shares bare module keys between the two
plugins; tests that introspect their OWN plugin's SOURCE through the bare
`pgconn` key (mnemo2's test_r8_ddl_serialization AST checks read
`pgconn.__file__`) must see mnemo-pg2's file — but in a combined run the
bare key belongs to lcm2-pg's module (see _bare_arbiter / _pin_plugin).
Rewriting the bare key per test is unsafe; instead this hook rewinds the
MODULE-GLOBAL `pgconn` binding of the mnemo2 test module itself, right
for its own tests, and restores it after. Runtime paths are untouched
(they bind through the conftest's package-qualified route).
"""
import importlib.util as _ilu
import os as _os
import sys as _sys

_REPO = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
_OWN_DIR = _os.path.realpath(_os.path.dirname(_os.path.abspath(__file__)))
_SIB_PLUGIN = _os.path.realpath(_os.path.join(_REPO, "plugins", "mnemo-pg2"))
_saved = {}


def _dir_ours(f):
    return _os.path.dirname(_os.path.realpath(f or "")) == _SIB_PLUGIN


def _own_pgconn():
    mod = _sys.modules.get("mnemo-pg2.pgconn")
    if mod is not None and _dir_ours(getattr(mod, "__file__", "")):
        return mod
    spec = _ilu.spec_from_file_location(
        "mnemo-pg2.pgconn", _os.path.join(_SIB_PLUGIN, "pgconn.py"))
    if spec is None or spec.loader is None:
        return None
    mod = _ilu.module_from_spec(spec)
    _sys.modules["mnemo-pg2.pgconn"] = mod
    try:
        spec.loader.exec_module(mod)
    except BaseException:
        _sys.modules.pop("mnemo-pg2.pgconn", None)
        raise
    return mod


def pytest_runtest_logstart(nodeid, location):
    path = location[0] or ""
    if not path.startswith("tests/mnemo2") or _saved:
        return
    modname = path[:-3].replace("/", ".") if path.endswith(".py") else ""
    mod = _sys.modules.get(modname)
    if mod is None or not _dir_eq(getattr(mod, "__file__", ""), _OWN_DIR):
        return
    cur = getattr(mod, "pgconn", None)
    if cur is not None and not _dir_ours(getattr(cur, "__file__", "")):
        _saved["m"] = (mod, cur)
        try:
            own = _own_pgconn()
        except Exception:
            own = None
        if own is not None:
            mod.__dict__["pgconn"] = own


def _dir_eq(f, want):
    return _os.path.realpath(f or "") == want


def pytest_runtest_logfinish(nodeid, location):
    if _saved:
        mod, prev = _saved.pop("m")
        try:
            mod.__dict__["pgconn"] = prev
        except Exception:
            pass
