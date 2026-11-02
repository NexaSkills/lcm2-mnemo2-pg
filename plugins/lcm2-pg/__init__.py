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

import importlib as _ilib
import importlib.abc as _ilabc
import importlib.util as _ilu
import os as _os
import sys as _sys

_HERE = _os.path.dirname(_os.path.abspath(__file__))
_PKG_MODULES = ("plugin_entry", "tools", "hooks", "schemas", "engine",
                "command", "pgconn", "ddl", "rollup_periods", "_selfimport")

# Names we may publish/resolve OURSELVES (bare, when we own the name).
_OWN_BARE = set(_PKG_MODULES)

# Exec-window depth cell — THE single instance for the whole interpreter.
# _selfimport.anchor() increments it around every exec_module it runs and
# __init__._SelfImportFinder consults it: while it is > 0 a bare import of
# one of OUR names may belong to our exec on ANY thread (worker threads,
# deferred calls) — a frame-stack check cannot see those, and importlib
# walks meta_path identically for a concurrent host import, so the cell
# (not the stack) is the honest authorization. Located by sys.modules KEY
# only — never `import`ed (an import of anything re-enters the finders
# and would recurse). Created on demand by _depth_cell() below.
_DEPTH_KEY = "_lcm2_depth_registry_ours"


def _depth_cell():
    c = _sys.modules.get(_DEPTH_KEY)
    if c is None:
        import types as _t
        c = _t.ModuleType(_DEPTH_KEY)
        setattr(c, "CELL", [0])
        _sys.modules[_DEPTH_KEY] = c
    return getattr(c, "CELL")


_DEPTH = _depth_cell()


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
        if _ENGAGED[0] <= 0 and _DEPTH[0] <= 0:
            return None
        # F-1 (P2FLIP): ORIGIN gate only when NO exec window is open.
        # _DEPTH > 0 (a _selfimport.anchor() exec is running somewhere in
        # the process) IS the authorization: the import belongs to our
        # exec (worker thread, deferred call, nested anchor). Frame stacks
        # mean nothing to a concurrent HOST-thread import (importlib walks
        # meta_path identically for `tools.<sub>` -> parent 'tools'), so
        # the counter is only ever consulted on OUR side of the race and
        # nothing here publishes a host-owned bare name anyway.
        if _DEPTH[0] <= 0 and not _origin_is_ours():
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


def _origin_is_ours():
    """True if any frame on the CURRENT import stack belongs to a module
    whose file lives in OUR plugin dir (the _selfimport._SibFinder
    predicate, mirrored so late anchor()-less windows gate identically).

    F-1 (P2CLOSE): importlib bootstrap frames are SKIPPED. `_bootstrap` /
    `_bootstrap_external` report __file__ inside the interpreter's own lib
    dir — which IS our dir under a symlinked plugin layout — so an ordinary
    host import chain (MainThread `import hermes_cli.models`) walking
    meta_path showed a "frame from our dir" and was served our flat file:
    the residual boot race. Only frames from real module FILES under a
    different interpreter lib count.
    """
    try:
        here = _os.path.realpath(_HERE)
    except Exception:
        return False
    try:
        fr = _sys._getframe(1)
    except Exception:
        return False
    while fr is not None:
        fname = getattr(fr, "f_code", None)
        fname = getattr(fname, "co_filename", "") if fname else ""
        base = _os.path.basename(str(fname))
        if base.startswith(("_bootstrap", "_frozen_importlib")):
            fr = fr.f_back
            continue
        fn = getattr(fr, "f_globals", {}).get("__file__", "")
        if isinstance(fn, str) and fn:
            try:
                if _os.path.dirname(_os.path.realpath(fn)) == here:
                    return True
            except Exception:
                pass
        fr = fr.f_back
    return False



_ENGAGED = [0]

# Cross-plugin import hygiene (qa3 N-3, the lcm2db half): if the sibling
# mnemo-pg2 suite's conftest already prepended plugins/mnemo-pg2 to
# sys.path (both suites sharing one pytest process), OUR OWN bare
# sibling imports could still bind mnemo-pg2's files through the path
# order — mnemo's connect(ddl=True) has no public-busy guard, so a stray
# call materialises the 40 vendor tables into lcm2db.public (the F3
# tripwire failure tessa saw in the combined suite). A meta_path finder
# in FRONT of PathFinder wins the name race WITHOUT touching sys.path
# (which the sibling suite owns); it answers only while an OUR-dir file
# sits earlier in the import stack than any other project dir, so a bare
# import from foreign code never resolves here (no hijack — the F-1
# discipline: probe frames, not publish blindly).
_PROJECT_ROOT = _os.path.dirname(_os.path.dirname(_HERE))


class _SibFinder:
    def find_spec(self, fullname, path=None, target=None):
        try:
            p = _os.path.join(_HERE, fullname + ".py")
            if not _os.path.exists(p):
                return None
            our_depth = for_depth = None
            fr = _sys._getframe(1)
            lib = _os.path.dirname(getattr(_sys, "base_prefix", ""))
            depth = 0
            while fr is not None and depth < 400:
                depth += 1
                fn = getattr(fr, "f_globals", {}).get("__file__", "")
                if isinstance(fn, str) and fn:
                    d = _os.path.dirname(_os.path.realpath(fn))
                    if our_depth is None and d == _os.path.realpath(_HERE):
                        our_depth = depth
                    if (for_depth is None and d != _os.path.realpath(_HERE)
                            and d.startswith(_os.path.realpath(
                                _PROJECT_ROOT)) and d != _os.path.realpath(
                                    _PROJECT_ROOT)):
                        for_depth = depth
                    elif (for_depth is None and d != _os.path.realpath(
                            _PROJECT_ROOT)
                            and not d.startswith(_os.path.realpath(
                                _PROJECT_ROOT))
                            and "/site-packages" not in d
                            and d != lib and not d.startswith(lib + "/")):
                        for_depth = depth  # host repo / external project
                fr = fr.f_back
            if our_depth is not None and (for_depth is None
                                          or our_depth <= for_depth):
                return _ilu.spec_from_file_location(fullname, p)
            return None
        except Exception:
            return None


if _os.path.basename(_HERE) == "lcm2-pg" and not any(
        type(f).__name__ == "_SibFinder" for f in _sys.meta_path):
    _sys.meta_path.insert(0, _SibFinder())


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


def _selfimport_module():
    """Our _selfimport.py by FILE under the namespaced key
    _lcm2_impl__selfimport. The register() purge DELETES the bare
    '_selfimport' key (boot hygiene), so every LATER call from host
    frames (hooks dispatching a turn) must resolve anchor() through a
    loader bound to our package, never the bare name. Package-relative
    import keeps _selfimport's own _PKG bookkeeping identical to the
    exec-window route (F-2 fix route b: package-qualified resolution).
    Falls back to the engine.py-maintained alias key, then to spec-by-
    file, so it works standalone too."""
    pkg = __package__ or ""
    if pkg:
        try:
            return _ilib.import_module(f"{pkg}._selfimport")
        except Exception:
            pass
    m = _sys.modules.get("_lcm2_impl__selfimport")
    if m is not None and getattr(m, "anchor", None) is not None:
        return m
    spec = _ilu.spec_from_file_location(
        "_lcm2_impl__selfimport", _os.path.join(_HERE, "_selfimport.py"))
    if spec is None or spec.loader is None:  # pragma: no cover
        raise ImportError(f"lcm2-pg: _selfimport.py missing in {_HERE}")
    mod = _ilu.module_from_spec(spec)
    _sys.modules["_lcm2_impl__selfimport"] = mod
    spec.loader.exec_module(mod)
    return mod


def _anchor(name):
    """Sibling module by file via _selfimport.anchor() resolved through a
    name that survives the register() purge (F-2: the bare '_selfimport'
    key is deleted at boot hygiene, so late calls from host frames use
    the package-qualified / namespaced routes in _selfimport_module())."""
    return _selfimport_module().anchor(name)


def _sibling(name):
    """Module binding for a late (post-register, host-origin) call: OUR
    file-anchored module always. sys.modules FIRST (the same identity
    register()'s exec-time anchor published — one code path, one module),
    then a pure package-qualified import (publishes only under OUR
    package name, no bare name), then anchor() as last resort. Never a
    plain bare `import <name>`: after the purge that binds the HOST's
    package (F-2 'context engine that does not inherit' / 'Unknown
    context engine tool' class of bug)."""
    m = _sys.modules.get(name)
    if _ours_bound(name):
        return m
    pkg = __package__ or ""
    if pkg:
        try:
            return _ilib.import_module(f"{pkg}.{name}")
        except Exception:
            pass
    return _anchor(name)


def register(ctx, engine_factory=None):
    """General plugin registration: tools + hooks + /lcm command + skills.

    Leaves the interpreter as it found it: every module key our exec
    CREATED is deleted (clear()/update() cannot do that — the surviving
    created key under a bare name was the F1 crash), pre-existing keys
    are byte-exactly restored (host bindings return), and no global
    sys.path entry is ever added or removed.
    """
    saved_mods = dict(_sys.modules)
    _disarm = _engage()  # bare sibling imports -> our files, by file
    # (F1 FIX-3: the old preemptive eviction loop for foreign bare holders
    # and the 1 ms _revert_loop restore-watch thread are GONE. Both widened
    # the race window: evicting a host binding it did not own made a
    # concurrent host `import tools.<sub>` see a half-restored sys.modules,
    # stranding 'tools' as non-package. The origin-gated _SelfImportFinder
    # (above) is the whole mechanism: nothing here edits sys.path or
    # removes foreign keys, so no racer can observe a foreign-bound bare
    # name as OUR file.)
    try:
        from _selfimport import anchor
        pe = anchor("plugin_entry")
        return pe.register(ctx, engine_factory=engine_factory)
    finally:
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
