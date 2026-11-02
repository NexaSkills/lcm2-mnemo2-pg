"""T-1 regression: mnemo-pg2 must never clobber the SHARED bridge key.

Boot crash (template gateway): "Failed to load plugin 'lcm2-pg': module
'_lcm2_pg_bridge.context_engine_shim' has no attribute
'LCM2PgContextEngine'". Root cause (FIX9 cousin — import-lane shadowing,
bridge-module vs file): mnemo-pg2's module-time bridge probe bound its
INERT marker file under the shared key whenever the key lacked mnemo's
(deliberately absent) class — true for any foreign-owned key — so any
load order with mnemo-pg2 before lcm2-pg's general package import left
the key pointing at an attribute-free module, and lcm2-pg's
module-time `_load_bridge` hit-test died.

Deterministic proof, no threads: import the two plugin packages (and the
context-engine discovery dir) as FILE modules in every order; lcm2-pg's
package import must always expose LCM2PgContextEngine, and the shared
key must resolve into lcm2-pg's dir once lcm2-pg has loaded.
"""
import importlib.util
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))          # tests/lcm2
ROOT = os.path.dirname(os.path.dirname(HERE))              # repo root
LCM2_DIR = os.path.join(ROOT, "plugins", "lcm2-pg")
MNEMO_DIR = os.path.join(ROOT, "plugins", "mnemo-pg2")
CE_DIR = os.path.join(ROOT, "plugins", "context_engine", "lcm2-pg")
SHARED_KEY = "_lcm2_pg_bridge.context_engine_shim"
_PROBE_KEYS = ("_t1_probe_lcm2", "_t1_probe_mnemo", "_t1_probe_ce_dir")
_ISOLATE_PREFIXES = ("_lcm2_pg_bridge", "_lcm2_pg_ce_pkg", "_lcm2_impl",
                    "_mnemo2_impl", "lcm2_pg_fs", "_lcm2_depth",
                    "_hermes_user", "_hermes_plugins")
_HOST_ROOT = os.path.expanduser("~/.hermes/hermes-agent")


def _host_on_path():
    """The shim's ContextEngine base needs the host repo importable
    (real-boot emulation, same contract as plugins/lcm2-pg/
    test_f1_sysmodules_purge.py)."""
    if os.path.isdir(_HOST_ROOT) and _HOST_ROOT not in sys.path:
        sys.path.append(_HOST_ROOT)


def _isolate():
    for k in list(sys.modules):
        if k in _PROBE_KEYS or k.startswith(_ISOLATE_PREFIXES):
            sys.modules.pop(k, None)
    sys.meta_path[:] = [f for f in sys.meta_path
                        if type(f).__name__ not in
                        ("_SelfImportFinder", "_SibFinder")]


def _load_file_module(name, dirpath):
    init = os.path.join(dirpath, "__init__.py")
    spec = importlib.util.spec_from_file_location(
        name, init, submodule_search_locations=[dirpath])
    assert spec is not None and spec.loader is not None, f"no spec for {init}"
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def _load_lcm2():
    return _load_file_module("_t1_probe_lcm2", LCM2_DIR)


def _load_mnemo():
    return _load_file_module("_t1_probe_mnemo", MNEMO_DIR)


def _load_ce_dir():
    return _load_file_module("_t1_probe_ce_dir", CE_DIR)


_ORDERS = {
    "lcm2,mnemo,ce": (_load_lcm2, _load_mnemo, _load_ce_dir),
    "lcm2,ce,mnemo": (_load_lcm2, _load_ce_dir, _load_mnemo),
    "mnemo,lcm2,ce": (_load_mnemo, _load_lcm2, _load_ce_dir),
    "mnemo,ce,lcm2": (_load_mnemo, _load_ce_dir, _load_lcm2),
    "ce,lcm2,mnemo": (_load_ce_dir, _load_lcm2, _load_mnemo),
    "ce,mnemo,lcm2": (_load_ce_dir, _load_mnemo, _load_lcm2),
}


def test_bridge_class_resolvable_in_every_import_order():
    _host_on_path()
    for label, steps in _ORDERS.items():
        _isolate()
        try:
            for step in steps:
                step()
        finally:
            pass
        lcm2 = sys.modules.get("_t1_probe_lcm2")
        assert lcm2 is not None, f"{label}: lcm2-pg package not loaded"
        assert getattr(lcm2, "LCM2PgContextEngine", None) is not None, (
            f"{label}: lcm2-pg package import lost "
            "LCM2PgContextEngine (shared bridge key clobbered)")
        shared = sys.modules.get(SHARED_KEY)
        if shared is not None:
            f = getattr(shared, "__file__", "") or ""
            assert os.path.dirname(os.path.realpath(f)) == os.path.realpath(
                LCM2_DIR), (
                f"{label}: shared key {SHARED_KEY} bound to a foreign "
                f"file ({f}); must resolve into lcm2-pg's dir once "
                "lcm2-pg's package is loaded")
        _isolate()


def test_mnemo_probe_leaves_no_shared_key_residue():
    """Loading ONLY mnemo-pg2 must not publish the shared key at all
    (its shim is an inert marker; a bare probe must not create a
    binding the host/lcm2-pg would later misresolve)."""
    _isolate()
    _load_mnemo()
    shared = sys.modules.get(SHARED_KEY)
    assert shared is None or (
        os.path.dirname(os.path.realpath(
            getattr(shared, "__file__", "") or "x"))
        == os.path.realpath(LCM2_DIR)), (
        f"mnemo-only load published {SHARED_KEY} -> "
        f"{getattr(shared, '__file__', None)}")
    residue = [k for k in sys.modules if "_ce_marker" in k]
    assert residue == [], f"probe residue keys left behind: {residue}"
    _isolate()
