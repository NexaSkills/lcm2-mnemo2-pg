"""Bare sibling imports -> THIS plugin's files (qa3 N-3 suite hygiene).

lcm2-pg ships modules with the SAME bare names (engine, pgconn, ddl,
plugin_entry). When both suites share one pytest process and lcm2's ran
first, the bare keys bind lcm2-pg's FILES, and a mnemo2 test's plain
`import pgconn` silently gets lcm2-pg's routes (agent_schema() ->
lcm2_<agent>, lcm2-pg's public-busy tripwire raising on mnemo2db):
the 6 ordering-coupled full-suite-only failures.

This module sits ON sys.path (installed by tests/mnemo2/conftest.py) and
wins the bare names through normal import machinery: a plain
`import pgconn` resolves to OUR pgconn.py by path (package-relative
siblings inside it import the same way). The module's own file identity
makes conftest's _ours() gate pass, so no further pinning applies. No
sys.modules editing, no meta_path hooks: the host's bindings are not
touched at all — this only steers imports issued inside the suite.
"""
import importlib.util as _ilu
import os as _os
import sys as _sys

_HERE = _os.path.dirname(_os.path.abspath(__file__))
_NAMES = ("engine", "pgconn", "ddl", "plugin_entry", "embed",
          "context_engine_shim", "memory_provider", "tool_catalog",
          "tools", "heuristics", "decay", "entity", "budget")


def _load(name):
    key = "_mnemo2_thru_" + name
    mod = _sys.modules.get(key)
    if mod is not None:
        return mod
    path = _os.path.join(_HERE, name + ".py")
    if not _os.path.exists(path):
        raise ImportError("mnemo-pg2: " + name + ".py missing in " + _HERE)
    spec = _ilu.spec_from_file_location(key, path)
    if spec is None or spec.loader is None:  # pragma: no cover
        raise ImportError("mnemo-pg2: cannot spec " + path)
    mod = _ilu.module_from_spec(spec)
    _sys.modules[key] = mod
    try:
        spec.loader.exec_module(mod)
    except BaseException:
        _sys.modules.pop(key, None)
        raise
    return mod


class _ThruFinder:
    """Answers the bare sibling names from THIS directory, by path.
    Last resort on sys.meta_path: it fires only when the cache and the
    standard finders miss (i.e. the key was never imported)."""

    def find_spec(self, fullname, path=None, target=None):
        if fullname not in _NAMES:
            return None
        try:
            return _ilu.spec_from_file_location(
                "_mnemo2_thru_" + fullname,
                _os.path.join(_HERE, fullname + ".py"))
        except Exception:
            return None


if not any(isinstance(f, _ThruFinder) for f in _sys.meta_path):
    _sys.meta_path.append(_ThruFinder())
