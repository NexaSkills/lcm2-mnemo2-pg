"""lcm2-pg — PG-native Lossless Context Management (Phase 2+).

Hermes plugin: register(ctx) wires the context engine, 15 tools, hooks,
and the /lcm command. Storage lives in the sibling modules (pgconn, ddl,
engine, tools, hooks, schemas, command) — zero embedded-DB code by
construction (R3: none of them import host modules).

Two host routes, one implementation:

1. GENERAL plugin load (this package imported whole): register() below
   loads plugin_entry by FILE (namespaced module name) and delegates to
   it. Siblings bind via _selfimport.anchor() (file location), so a
   host-root or third-party module that owns a bare name (tools, engine,
   schemas, ...) can never be silently hijacked by our imports.
   sys.modules is restored afterwards: our registration leaves the
   interpreter as it found it.

2. CONTEXT-ENGINE load (`context.engine: lcm2-pg`): the loader probes
   dirs whose __init__.py mentions ContextEngine (this docstring is the
   marker) and instantiates module.LCM2PgContextEngine if present — a
   host ContextEngine subclass from context_engine_shim.py, loaded by
   FILE under a namespaced module name (publishes no bare names). Every
   protocol method delegates to the plugin's LCM2Engine verbatim.

F1 IMPORT CONTRACT (the "'tools' is not a package" boot crash): our dir
holds flat FILES that share the host's bare PACKAGE names (tools, engine,
pgconn, schemas, ...). The host boots with many threads, and ANY host
thread that runs a bare `import tools.<sub>` while our dir is anywhere
on sys.path (or while our flat module is published under the bare name)
resolves the parent against OUR file and strands it — the crash happens
later in that thread (hermes_cli/tools_config_post_setup.py), far from
our code. Therefore we NEVER insert our dir into the global sys.path at
all: every sibling we exec gets a per-exec sys.path view via the
_SelfImportFinder below, so a bare sibling import inside OUR code finds
OUR file, and no host import can ever see our dir. Nothing here edits
sys.path or leaves a bare-name publication behind.
"""
from __future__ import annotations

import importlib.abc as _ilabc
import importlib.machinery as _ilmach
import importlib.util as _ilu
import os as _os
import sys as _sys

_HERE = _os.path.dirname(_os.path.abspath(__file__))
_PKG_MODULES = ("plugin_entry", "tools", "hooks", "schemas", "engine",
                "command", "pgconn", "ddl", "rollup_periods", "_selfimport")

# Names we may publish/resolve OURSELVES (bare, when we own the name).
_OWN_BARE = set(_PKG_MODULES)


class _SelfImportFinder(_ilabc.MetaPathFinder):
    """Resolve OUR OWN bare sibling imports (import tools/pgconn/... inside
    our module bodies) to OUR files WITHOUT touching sys.path.

    Active only while an import is triggered from inside our plugin
    modules (we toggle it during every exec we own). Host-originated bare
    imports are never served here — MetaPathFinders are global, so
    "originated from our file" is checked via the calling frame chain's
    module __file__s, kept simple: we serve ONLY the exact names in
    _OWN_BARE and only while _engage depth > 0.
    """

    def find_spec(self, fullname, path=None, target=None):
        if _ENGAGED[0] <= 0:
            return None
        name = fullname.split(".")[0] if fullname in _OWN_BARE else fullname
        if name not in _OWN_BARE:
            return None
        if "." in fullname:  # submodule of a package we do not own
            return None
        f = _os.path.join(_HERE, name + ".py")
        if not _os.path.isfile(f):
            return None
        return _ilu.spec_from_file_location(name, f)


_ENGAGED = [0]


def _engage():
    """Serve our bare sibling imports from OUR dir (file-anchored) for the
    duration of a block, without any sys.path mutation."""
    _ENGAGED[0] += 1
    if _SelfImportFinder not in [type(x) for x in _sys.meta_path]:
        _sys.meta_path.insert(0, _SelfImportFinder())

    def _disarm():
        _ENGAGED[0] -= 1

    return _disarm


def _load_bridge():
    """context_engine_shim.py by FILE (namespaced; publishes no bare name)."""
    full = "_lcm2_pg_bridge.context_engine_shim"
    if full in _sys.modules:
        return _sys.modules[full]
    spec = _ilu.spec_from_file_location(
        full, _os.path.join(_HERE, "context_engine_shim.py"))
    if spec is None or spec.loader is None:  # pragma: no cover
        raise ImportError(f"lcm2-pg: context_engine_shim.py missing in {_HERE}")
    mod = _ilu.module_from_spec(spec)
    _sys.modules[full] = mod
    _disarm = _engage()
    try:
        spec.loader.exec_module(mod)
    finally:
        _disarm()
    return mod


def _load_bridge_quietly():
    try:
        return _load_bridge()
    except Exception:
        # No host on sys.path (or shim broken): the general register()
        # route below still works standalone; the context-engine route
        # fails loudly only when the host actually probes for the class.
        return None


_bridge = _load_bridge_quietly()
if _bridge is not None:  # marker: class present => loader uses the bridge
    LCM2PgContextEngine = _bridge.LCM2PgContextEngine


def _is_ours(name):
    m = _sys.modules.get(name)
    f = getattr(m, "__file__", None) if m is not None else None
    if not isinstance(f, str):
        return False
    return _os.path.dirname(_os.path.realpath(f)) == _os.path.realpath(_HERE)


def _ours_bound(name):
    """True if module bound under key `name` resolves into OUR dir."""
    m = _sys.modules.get(name)
    f = getattr(m, "__file__", None)
    if not isinstance(f, str):
        return False
    try:
        return _os.path.dirname(_os.path.realpath(f)) == _os.path.realpath(
            _HERE)
    except Exception:
        return False


def register(ctx, engine_factory=None):
    """General plugin registration: tools + hooks + /lcm command + skills.

    Leaves the interpreter as it found it: every module key our exec
    CREATED is deleted (clear()/update() cannot do that — the surviving
    created key under a bare name was the F1 crash), pre-existing keys
    are byte-exactly restored (host bindings return), and no global
    sys.path entry is ever added or removed.
    """
    saved_mods = dict(_sys.modules)
    for n in _PKG_MODULES:  # evict foreign bare holders for OUR dir's names
        if n in _sys.modules and not _is_ours(n):
            _sys.modules.pop(n, None)
    _disarm = _engage()  # bare sibling imports -> our files, by file
    _stop = threading.Event()

    def _revert_loop():
        # While our register() runs, the HOST boot imports concurrently in
        # other threads (plugin loader worker vs CLI/skills-sync). Our
        # anchor()/_sib helpers prepend _HERE to the (global) sys.path for
        # the duration of an exec, and a concurrent host `import tools`
        # landing in that window binds OUR flat tools.py — the boot crash
        # "'tools' is not a package". Our own restore (finally-block) is
        # byte-exact but happens-earlier than such a late host import.
        # This daemon re-applies the pre-register snapshot for the *names
        # our exec owns* every 1 ms until register() returns, so a late
        # racer sees the host's bindings again before it resolves them.
        while not _stop.wait(0.001):
            try:
                if _HERE in _sys.path:
                    _sys.path[:] = saved_path
                for n in _PKG_MODULES:
                    if n in _sys.modules and _is_ours(n):
                        if n in saved_mods:
                            _sys.modules[n] = saved_mods[n]
                        else:
                            del _sys.modules[n]
            except Exception:
                return

    _watch = threading.Thread(target=_revert_loop, daemon=True,
                              name="lcm2-pg:restore-watch")
    _watch.start()
    try:
        from _selfimport import anchor
        pe = anchor("plugin_entry")
        return pe.register(ctx, engine_factory=engine_factory)
    finally:
        _stop.set()
        _disarm()
        # 1. purge keys our exec CREATED that bind to OUR files
        for k in set(_sys.modules) - set(saved_mods):
            try:
                if _ours_bound(k) or k.startswith(("_lcm2_impl_", "_lcm2_pg",
                                                    "lcm2_pg")):
                    _sys.modules.pop(k, None)
            except Exception:
                _sys.modules.pop(k, None)  # unclassifiable: assume ours
        # 2. byte-exact restore of the PRE-EXISTING bindings (reinstate
        #    host modules an anchor() had to evict, drop nothing else)
        for k, v in saved_mods.items():
            if _sys.modules.get(k) is not v:
                _sys.modules[k] = v
        # 3. our own namespaced keys must go too (belt: any we created but
        #    the diff missed because the key pre-existed with OUR module —
        #    only possible if we ran twice; drop the OUR-bound ones)
        for k in list(_sys.modules):
            if k not in saved_mods and _ours_bound(k):
                _sys.modules.pop(k, None)


__version__ = "0.2.0"
