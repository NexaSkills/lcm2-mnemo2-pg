"""Bare sibling imports -> THIS plugin's files (qa3 N-3 suite hygiene).

lcm2-pg ships modules with the SAME bare names (engine, pgconn, ddl,
plugin_entry). When both suites share one pytest process and lcm2's ran
first, a mnemo2 test's plain `import pgconn` binds lcm2-pg's FILE — or
a spec-less alias module manufactured over it by an anchor helper that
couldn't locate a file — so mnemo2 code silently runs lcm2-pg's routes:
agent_schema() -> lcm2_<agent>, lcm2-pg's public-busy tripwire raising
on mnemo2db. Those are the 6 ordering-coupled, full-suite-only failures
tessa filed.

conftest imports THIS module and prepends our directory to sys.path, so
imports issued from here on resolve to OUR files. On top of that, every
bare key that ALREADY names a foreign module gets a proxy installed at
its own key: a module whose attributes all delegate to OUR copy (loaded
by path, one instance under the package-qualified key). `import pgconn`
anywhere in the suite then serves a module that *is* our file's surface
— `pgconn.agent_schema()` runs OUR route. The proxies are never handed
to any other key (nothing published, nothing stolen), our own bindings
are left byte-identical (object identity: one Engine, F-1 invariant),
and the process-wide import cache keeps the host's names — register()'s
purge discipline, the rule test_f1_own_registration pins.
"""
import importlib.util as _ilu
import os as _os
import sys as _sys

_HERE = _os.path.dirname(_os.path.abspath(__file__))
_PKG = _os.path.basename(_HERE)          # plugin dir name ("mnemo-pg2")
_NAMES = ("engine", "pgconn", "ddl", "plugin_entry", "embed",
          "context_engine_shim", "memory_provider")
_OURS = "_mnemo2_ours"                   # marker attr on our copies


def _dir_of(f):
    return _os.path.realpath(_os.path.dirname(f or ""))


def _foreign_bound(name):
    """True when the bare key names something that is NOT our file. A
    spec-less binding (find_spec raises ValueError — the poison
    signature) counts as foreign unless it carries our marker."""
    cur = _sys.modules.get(name)
    if cur is None or getattr(cur, _OURS, False):
        return False
    if _dir_of(getattr(cur, "__file__", "") or "") == _os.path.realpath(_HERE):
        return False  # already OUR file: leave the binding (identity)
    try:
        origin = _ilu.find_spec(name)
    except (ImportError, ValueError, AttributeError, KeyError):
        return True  # spec-less alias: nothing proves it is ours
    if origin is None:
        return False  # key gone mid-flight: nothing to proxy
    return _dir_of(getattr(origin, "origin", "") or "") != \
        _os.path.realpath(_HERE)


def _load_ours(name):
    """OUR file for `name`, by path, ONE copy under the package-
    qualified key (reuses the twin register()'s purge kept, if present)."""
    key = _PKG + "." + name
    cand = _sys.modules.get(key)
    if cand is not None and _dir_of(getattr(cand, "__file__", "")
                                    or "") == _os.path.realpath(_HERE):
        return cand
    path = _os.path.join(_HERE, name + ".py")
    if not _os.path.exists(path):
        raise ImportError("mnemo-pg2: " + name + ".py missing in " + _HERE)
    spec = _ilu.spec_from_file_location(key, path)
    if spec is None or spec.loader is None:  # pragma: no cover
        raise ImportError("mnemo-pg2: cannot spec " + path)
    mod = _ilu.module_from_spec(spec)
    _sys.modules[key] = mod
    try:
        spec.loader.exec_module(mod)  # module-level sibling imports land
                                      # on OUR files via conftest's path
        setattr(mod, _OURS, True)
    except BaseException:
        _sys.modules.pop(key, None)
        raise
    return mod


def _proxy(name, ours):
    """A module bound at the foreign key's slot whose whole surface
    delegates to `ours` (PEP 562 module __getattr__) while __file__/
    __spec__ keep pointing at our file for origin checks."""
    import types as _t
    prox = _t.ModuleType(name)
    prox.__dict__["__getattr__"] = lambda attr, _o=ours: getattr(_o, attr)
    prox.__file__ = getattr(ours, "__file__", None)
    prox.__spec__ = getattr(ours, "__spec__", None)
    prox.__doc__ = getattr(ours, "__doc__", None)
    setattr(prox, _OURS, True)
    return prox


for _n in _NAMES:
    try:
        if _foreign_bound(_n):
            _sys.modules[_n] = _proxy(_n, _load_ours(_n))
    except Exception:  # a missing optional module must not break the rest
        pass
