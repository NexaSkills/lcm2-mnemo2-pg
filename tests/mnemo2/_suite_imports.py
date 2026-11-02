"""N-3 suite hygiene: OUR files win in a shared-process run (qa3 N-3).

lcm2-pg ships modules with the SAME bare names (engine, pgconn, ddl,
plugin_entry). When both suites share one pytest process, whichever
conftest runs first seeds the bare sys.modules keys; a mnemo2 test's
plain `import pgconn` then binds lcm2-pg's FILE and mnemo2 code silently
runs lcm2-pg's routes (agent_schema() -> lcm2_<agent>, lcm2-pg's
public-busy tripwire raising on mnemo2db) — the ordering-coupled,
full-suite-only failures tessa filed.

lcm2's conftest pins its own files by inserting ITS plugin dir at
sys.path[0] (path order decides). We mirror that symmetrically: THIS
file (imported by our conftest) drops lcm2-pg's plugin dir BELOW
OUR plugin dir in sys.path. After this point a plain bare import from
suite code resolves through path order to OUR file — no sys.modules
edits, no meta_path hooks, nothing added to the host. Our conftest
already prepends OUR plugin dir; this only removes the advantage the
lcm2 conftest took. If lcm2's dir is not on the path at all (mnemo2
suite standalone), this is a no-op.
"""
import os as _os
import sys as _sys

_HERE = _os.path.dirname(_os.path.abspath(__file__))           # tests/mnemo2
_REPO = _os.path.dirname(_os.path.dirname(_HERE))              # repo root
_OUR_DIR = _os.path.join(_REPO, "plugins", "mnemo-pg2")
_SIBLING_DIR = _os.path.join(_REPO, "plugins", "lcm2-pg")

_OUR_R = _os.path.realpath(_OUR_DIR)
_SIB_R = _os.path.realpath(_SIBLING_DIR)

_path = list(_sys.path)
_path = [p for p in _path if _os.path.realpath(p or "") != _SIB_R]
try:
    _ours_at = next(i for i, p in enumerate(_path)
                    if _os.path.realpath(p or "") == _OUR_R)
except StopIteration:  # our dir somehow absent: prepend it explicitly
    _path.insert(0, _OUR_DIR)
    _ours_at = 0
_path.insert(_ours_at + 1, _SIBLING_DIR)  # below ours, still importable
_sys.path[:] = _path
