"""fix6 N-3 (DEFINITIVE): the SIBLING suite's cross-suite import machinery
must not run at all inside a combined pytest process.

WHY (evidence: qa4 N-3 + the combined-run tracebacks): mnemo-pg2's
_suite_imports/_public_imports rewrite sys.modules BARE keys
(engine/pgconn/ddl/plugin_entry/embed/...) with proxies onto mnemo's
files "so `import pgconn` anywhere in the suite serves OUR surface".
Their import machinery (register-time anchor(), worker imports,
memory_provider internals) works through those bare keys by design, so
the rewrite is PROCESS-WIDE by necessity. In a combined run an lcm2
test's lazy `import engine` then hits the proxy before any meta_path
finder (sys.modules hit short-circuits finders) and gets mnemo's engine
('module _mnemo2_impl_engine' has no attribute LCM2Engine) — 40 errors.
No per-test finder or pin can out-race a process-wide key rewrite that
the sibling re-installs whenever its conftest is collected.

DISARM: importing a sibling module through THIS shim yields the real
file executed with the machinery stubbed inert (empty modules with the
marker attrs the guards read). Consequences, checked against the qa4
matrix:
* mnemo2's OWN suite files import bare names with their conftest's
  sys.path prepend in effect -> PathFinder answers mnemo's files
  directly. Identical files, identical behavior.
* The machinery was only ever load-bearing for cross-suite pollution
  defense — which is exactly the pollution it CAUSES in this repo's
  combined lane. In an isolated mnemo2 run it is inert by its own
  _foreign_bound() predicate (nothing foreign bound). So disarming it
  inside the combined process cannot change isolated-run semantics.
* The shim is TEST-LANE ONLY: it lives under tests/ and is keyed by the
  sibling's file path; the host's production import of mnemo-pg2 is
  untouched (F-1 discipline preserved).
"""
import importlib.util as _ilu
import os as _os
import sys as _sys
import types as _types

_REPO = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
_SIB = _os.path.realpath(_os.path.join(_REPO, "plugins", "mnemo-pg2"))
_INERT = ("_suite_imports",)          # module(s) whose side effects disarm
_MARKER_ATTRS = ("_OURS",)             # markers other guards getattr()


def _inert_mod(name):
    m = _types.ModuleType("mnemo2_disarmed." + name)
    m.__file__ = _os.path.join(_SIB, name + ".py")
    for a in _MARKER_ATTRS:
        setattr(m, a, "_ours_" + name)
    m.__disarmed__ = True
    return m


def _finder_hook():
    """Install a meta_path finder that serves the sibling's INERT
    machinery modules by their exact file paths (bare or package-
    qualified), before PathFinder. Everything else passes through."""
    holder = _sys.modules.get("_mnemo2_disarm_hook")
    if holder is not None:
        return

    class _Disarm:
        def find_spec(self, fullname, path=None, target=None):
            # importlib passes path=None for a TOP-LEVEL (bare) import.
            # Disarm exactly that case: a bare `import _suite_imports`
            # made while the sibling dir is simply on sys.path. Submodule
            # imports (path=list/str) resolve via parent __path__ and are
            # never this machinery's route.
            try:
                if path is not None:
                    return None
                top = fullname.rsplit(".", 1)[-1]
                if top not in _INERT or "." in fullname:
                    return None
                cand = _os.path.join(_SIB, top + ".py")
                if not _os.path.isfile(cand):
                    return None
                return _ilu.spec_from_loader(
                    fullname, _Loader(fullname))
            except Exception:
                return None

    class _Loader:
        def __init__(self, name):
            self._name = name

        def create_module(self, spec):
            return _inert_mod(self._name.rsplit(".", 1)[-1])

        def exec_module(self, module):
            pass

    holder = _types.ModuleType("_mnemo2_disarm_hook")
    holder.__dict__["finder"] = _Disarm()
    _sys.modules["_mnemo2_disarm_hook"] = holder
    _sys.meta_path.insert(0, holder.finder)
