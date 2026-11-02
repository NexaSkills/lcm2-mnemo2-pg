"""Import shim for the mnemo2 suite (qa3 N-3: ordering-coupled failures).

lcm2-pg ships modules with the SAME bare names (pgconn, engine, ddl,
plugin_entry). When both suites share one pytest process and lcm2's ran
first, the bare keys in sys.modules bind lcm2-pg's FILES, so a mnemo2
test's `import pgconn` silently gets lcm2-pg's routes (agent_schema()
-> lcm2_<agent>; lcm2-pg's public-busy tripwire raising on mnemo2db —
the 6 full-suite-only failures). register_agent's namespaced package
import is unaffected; only bare test-level imports were exposed.

This shim sits ON sys.path (installed by tests/mnemo2/conftest.py) and
wins the bare names through normal import machinery:
  * import already bound to OUR file -> passthrough (identical object,
    one Engine — the F-1 identity invariant);
  * import bound to a FOREIGN file  -> load OUR file by path under a
    namespaced key and serve it, without ever editing the foreign
    binding itself.

No sys.path or sys.modules mutation at import time: the finder answers
imports lazily, so the host's bindings stay byte-exact (register()'s
purge discipline parity; test_f1_own_registration's no-theft rule).
"""
import importlib.util as _ilu
import os as _os
import sys as _sys

HERE = _os.path.dirname(_os.path.abspath(__file__))
NAMES = frozenset(("engine", "pgconn", "ddl", "plugin_entry", "embed",
                   "context_engine_shim", "memory_provider"))
_IMPL = "_mnemo2_shim_"


def _here(mod):
    f = getattr(mod, "__file__", "") or ""
    return bool(f) and _os.path.realpath(
        _os.path.dirname(f)) == _os.path.realpath(HERE)


def _file_loader():
    """OUR _selfimport copy (never the bare '_selfimport' key — the F-1
    hijack class: lcm2-pg ships a loader under that name too)."""
    cand = _sys.modules.get("_mnemo2_impl__selfimport")
    if cand is not None and hasattr(cand, "anchor") and _here(cand):
        return cand
    key = _IMPL + "_selfimport"
    mod = _sys.modules.get(key)
    if mod is not None and hasattr(mod, "anchor"):
        return mod
    spec = _ilu.spec_from_file_location(
        key, _os.path.join(HERE, "_selfimport.py"))
    if spec is None or spec.loader is None:  # pragma: no cover
        raise ImportError("mnemo-pg2: _selfimport.py missing in " + HERE)
    mod = _ilu.module_from_spec(spec)
    _sys.modules[key] = mod
    spec.loader.exec_module(mod)
    return mod


def _ours(name):
    """Our file for `name`, by path: prefer the object register()'s exec
    already anchored (identity parity), else file-load it ourselves."""
    for k in (name, "_mnemo2_impl_" + name):
        cand = _sys.modules.get(k)
        if cand is not None and _here(cand):
            return cand
    key = _IMPL + name
    mod = _sys.modules.get(key)
    if mod is not None:
        return mod
    spec = _ilu.spec_from_file_location(key, _os.path.join(HERE,
                                                          name + ".py"))
    if spec is None or spec.loader is None:  # pragma: no cover
        raise ImportError("mnemo-pg2 shim: " + name + ".py missing")
    mod = _ilu.module_from_spec(spec)
    _sys.modules[key] = mod
    try:
        spec.loader.exec_module(mod)  # module's own bare siblings resolve
                                      # through our anchor / this shim
    except BaseException:
        _sys.modules.pop(key, None)
        raise
    return mod


class _SuiteShimFinder:
    """Serves the suite-scope bare sibling names, foreign keys never
    mutated (see module docstring)."""

    def find_spec(self, fullname, path=None, target=None):
        if fullname not in NAMES:
            return None
        cur = _sys.modules.get(fullname)
        if cur is not None and _here(cur):
            return None  # already OURS: normal machinery serves the twin
        try:
            ours = _ours(fullname)
        except ImportError:
            return None  # not shipped in this layout: fall through
        spec = _ilu.spec_from_loader(
            fullname, _SuiteShimLoader(fullname, ours))
        return spec


class _SuiteShimLoader:
    def __init__(self, name, mod):
        self._name, self._mod = name, mod

    def create_module(self, spec):
        return self._mod  # reuse OUR module object (no second copy)

    def exec_module(self, module):
        pass  # already fully executed

    def load_module(self, fullname):  # legacy LoaderProtocol conformance
        _sys.modules[fullname] = self._mod
        return self._mod


_INSTALLED = False


def install():
    global _INSTALLED
    if _INSTALLED:
        return
    if not any(isinstance(f, _SuiteShimFinder) for f in _sys.meta_path):
        _sys.meta_path.insert(0, _SuiteShimFinder())
    _INSTALLED = True


install()
