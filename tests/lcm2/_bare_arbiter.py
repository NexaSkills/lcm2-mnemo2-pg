"""Shared bare-key arbitration for the combined pytest process (fix6 N-3).

Both suites ship files with the SAME bare module names (engine, pgconn,
ddl, plugin_entry, embed, ...). Whichever suite binds a bare key first
KEEPS it: a later `import engine` returns the cached module whatever the
importing test's sys.path says. That is the qa4-N-3 / iso-run
"module '_mnemo2_impl_engine' has no attribute 'LCM2Engine'" class: an
lcm2 test's lazy `import engine` served mnemo's proxy (and the mirror
class poisoned mnemo's r7/shared rows when mnemo lost the race instead).
The per-suite machinery (lcm2's _SibFinder gate, mnemo's _suite_imports
proxies) routes imports issued from ITS OWN plugin files correctly — a
TEST module is "foreign" to both, so both lose on bare keys grabbed by
the other.

Arbitration is by importer provenance:

  * import stack touches tests/lcm2/ only    -> bind OUR file (lcm2-pg)
  * import stack touches tests/mnemo2/ only  -> no-op (their binding wins)
  * touches both / neither / host            -> no-op (leave as-is)

Only the shared bare names are arbitrated, only when the key is already
bound to one of the two plugins' files and lcm2's own dir is NOT ahead
on sys.path (if it is, PathFinder answers lcm2-pg anyway — same file,
same spec identity, no double exec). Installed once per process (marker
in sys.modules) by each suite conftest; adds no sys.path entry and
publishes nothing.
"""
import importlib.util as _ilu
import os as _os
import sys as _sys

MARKER = "_lcm2_mnemo2_bare_arbiter"

_OWN_DIR = _os.path.dirname(_os.path.abspath(__file__))            # tests/lcm2
_SIB_DIR = _os.path.join(_os.path.dirname(_OWN_DIR), "mnemo2")     # tests/mnemo2
_REPO = _os.path.dirname(_os.path.dirname(_OWN_DIR))
_OUR_PLUGIN = _os.path.join(_REPO, "plugins", "lcm2-pg")
_THEIR_PLUGIN = _os.path.realpath(_os.path.join(_REPO, "plugins", "mnemo-pg2"))
SHARED = ("engine", "pgconn", "ddl", "plugin_entry", "embed",
          "context_engine_shim", "memory_provider")
# names the SIBLING plugin ships (a name we don't have is never ours to
# steal back: there is nothing to bind):
_THEIR_NAMES = set()
try:
    _THEIR_NAMES = {f[:-3] for f in _os.listdir(_THEIR_PLUGIN)
                    if f.endswith(".py")}
except Exception:
    pass


def _real(p):
    try:
        return _os.path.realpath(p)
    except Exception:
        return ""


def _side_of(fn):
    """'lcm2' / 'mnemo2' if fn is a TEST file (not plugin, not conftest)
    of that suite; None otherwise (plugin internals, conftests, host)."""
    d = _os.path.dirname(_real(fn))
    b = _os.path.basename(str(fn))
    if d == _real(_OWN_DIR) or d == _real(_OUR_PLUGIN):
        side = "lcm2"
    elif d == _real(_SIB_DIR) or d == _THEIR_PLUGIN:
        side = "mnemo2"
    else:
        return None
    if "conftest" in b or d == _real(_OUR_PLUGIN) or d == _THEIR_PLUGIN:
        return None          # conftest/plugin frames carry no vote
    return side


class _BareArbiter:
    def find_spec(self, fullname, path=None, target=None):
        try:
            if "." in fullname or fullname not in SHARED:
                return None
            if fullname not in _THEIR_NAMES:
                return None
            cur = _sys.modules.get(fullname)
            if cur is None:
                return None
            f = getattr(cur, "__file__", "") or ""
            d = _real(f)
            if d == _real(_OUR_PLUGIN):
                return None                       # already ours: no-op
            if d != _THEIR_PLUGIN:
                return None                       # host/other owner: hands off
            ours = _os.path.join(_OUR_PLUGIN, fullname + ".py")
            if not _os.path.isfile(ours):
                return None
            # our dir ahead on sys.path => PathFinder would answer lcm2-pg
            # anyway; only intervene when the sibling dir is winning.
            if any(_real(p) == _real(_OUR_PLUGIN) for p in _sys.path if p):
                return None
            # provenance: deepest frame (the module that WRITES the import
            # statement) must be an lcm2 TEST file; a mnemo2 test frame
            # first, or the walk ending in plugin/host frames => no-op.
            fr = _sys._getframe(1)
            depth = 0
            while fr is not None and depth < 400:
                depth += 1
                fn = getattr(fr.f_globals, "__file__", "") or getattr(
                    fr.f_code, "co_filename", "")
                if fn:
                    d = _os.path.dirname(_real(fn))
                    if d in (_real(_OUR_PLUGIN), _THEIR_PLUGIN):
                        return None              # plugin-origin: not ours
                    if d == _real(_SIB_DIR):
                        return None              # sibling test: leave alone
                    if d == _real(_OWN_DIR):
                        b = _os.path.basename(str(fn))
                        if "conftest" in b:
                            return None          # our conftest: leave alone
                        return _ilu.spec_from_file_location(fullname, ours)
                fr = fr.f_back
            return None                          # host/tooling import
        except Exception:
            return None


def install():
    if _sys.modules.get(MARKER) is not None:
        return
    import types
    holder = types.ModuleType(MARKER)
    finder = _BareArbiter()
    holder.__dict__["finder"] = finder
    _sys.modules[MARKER] = holder
    _sys.meta_path.append(finder)
