"""mnemo-pg2 — PG-native mnemosyne memory provider on mnemo2db.

Hermes plugin: register(ctx) wires the vendor-parity 42-tool mnemosyne_*
surface and the lifecycle hooks. Storage lives in the sibling modules
(pgconn, ddl, engine, plugin_entry) — zero embedded-DB code by
construction (R3: none of them import host modules).

ONE host route: GENERAL plugin load (this package imported whole):
register() below loads plugin_entry by FILE (namespaced module name) and
delegates to it. Siblings bind via _selfimport.anchor() (file location),
so a host-root or third-party module that owns a bare name (tools,
engine, schemas, ...) can never be silently hijacked by our imports.
sys.modules is restored afterwards: our registration leaves the
interpreter as it found it. mnemo-pg2 is a MEMORY provider, NOT a
context/compression engine — it deliberately registers no context engine
(this docstring carries no ContextEngine marker on purpose).

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
_PKG_MODULES = ("plugin_entry", "engine", "pgconn", "ddl", "embed",
                "_selfimport")

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
_DEPTH_KEY = "_mnemo2_depth_registry_ours"


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


def _engage():
    """Serve our bare sibling imports from OUR dir (file-anchored) for the
    duration of a block, without any sys.path mutation."""
    _ENGAGED[0] += 1
    if _SelfImportFinder not in [type(x) for x in _sys.meta_path]:
        _sys.meta_path.insert(0, _SelfImportFinder())

    def _disarm():
        _ENGAGED[0] -= 1

    return _disarm


_BRIDGE_CLASS = "MnemoPg2ContextEngine"


def _load_bridge():
    """context_engine_shim.py by FILE (namespaced; publishes no bare name).

    Phase-3 (spec §3): mnemo-pg2 is a MEMORY plugin — no context-engine
    bridge file ships in this dir (that is lcm2-pg's route). If the
    shared key is already present it must ALSO carry our bridge class,
    else it belongs to another plugin (lcm2-pg loaded first in the same
    interpreter): we drop the stale shared key, re-load OUR shim file,
    and restore the foreign module afterwards so the other plugin keeps
    its binding (phase-2 import discipline: never leave a foreign-owned
    bare/bridge key modified). Absent file or absent class -> None: the
    general register() route works standalone either way.
    """
    full = "_lcm2_pg_bridge.context_engine_shim"
    foreign = _sys.modules.get(full)
    if foreign is not None and hasattr(foreign, _BRIDGE_CLASS):
        return foreign
    spec = _ilu.spec_from_file_location(
        full, _os.path.join(_HERE, "context_engine_shim.py"))
    if spec is None or spec.loader is None:  # pragma: no cover
        raise ImportError(f"mnemo-pg2: context_engine_shim.py missing in {_HERE}")
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
if _bridge is not None and hasattr(_bridge, _BRIDGE_CLASS):
    # marker: class present => loader uses the bridge. BY DESIGN there is
    # no context-engine class in mnemo-pg2's shim (memory plugin, spec §3):
    # the marker stays absent so the host context loader never picks us.
    LCM2PgContextEngine = _bridge.MnemoPg2ContextEngine


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
    _mnemo2_impl__selfimport. The register() purge DELETES the bare
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
    m = _sys.modules.get("_mnemo2_impl__selfimport")
    if m is not None and getattr(m, "anchor", None) is not None:
        return m
    spec = _ilu.spec_from_file_location(
        "_mnemo2_impl__selfimport", _os.path.join(_HERE, "_selfimport.py"))
    if spec is None or spec.loader is None:  # pragma: no cover
        raise ImportError(f"mnemo-pg2: _selfimport.py missing in {_HERE}")
    mod = _ilu.module_from_spec(spec)
    _sys.modules["_mnemo2_impl__selfimport"] = mod
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
    """General plugin registration: 42 tools (memory toolset) + 3 hooks.

    NO context engine is registered (memory plugin, spec §3 — the host
    context slot stays lcm2-pg's; R10: two lossless engines in one slot).

    Leaves the interpreter as it found it: every module key our exec
    CREATED is deleted (clear()/update() cannot do that — the surviving
    created key under a bare name was the F1 crash), pre-existing keys
    are byte-exactly restored (host bindings return), and no global
    sys.path entry is ever added or removed.
    """
    saved_mods = dict(_sys.modules)
    _disarm = _engage()  # bare sibling imports -> our files, by file
    # F-1 (generalized '_selfimport' hijack): a bare-key cache hit beats
    # meta_path finders — sys.modules is consulted FIRST. lcm2-pg boots
    # before us and publishes its pgconn (engine, ddl, ...) under the
    # SAME bare names, so a plain `import pgconn` inside our exec would
    # silently run lcm2's module (wrong agent_schema derivation, wrong
    # connect() signature — the live mnemo2agent failure class). Pin
    # every sibling we own to OUR file BEFORE anchoring plugin_entry;
    # anchor()'s eviction bookkeeping + the finally-block byte-exact
    # restore below make the whole swap undone at exit, exactly like the
    # '_selfimport' fix pinned via _selfimport_module().
    _si = _selfimport_module()
    for _n in _PKG_MODULES:
        if _n == "_selfimport":
            continue  # already ours via _selfimport_module()
        try:
            if not _ours_bound(_n):
                _si.anchor(_n)  # file-anchored; never a bare-key hijack
        except Exception:
            pass  # a missing sibling surfaces at its real use site
    # (F1 FIX-3: the old preemptive eviction loop for foreign bare holders
    # and the 1 ms _revert_loop restore-watch thread are GONE. Both widened
    # the race window: evicting a host binding it did not own made a
    # concurrent host `import tools.<sub>` see a half-restored sys.modules,
    # stranding 'tools' as non-package. The origin-gated _SelfImportFinder
    # (above) is the whole mechanism: nothing here edits sys.path or
    # removes foreign keys, so no racer can observe a foreign-bound bare
    # name as OUR file.)
    try:
        # F-1 ROOT CAUSE: the bare '_selfimport' key is shared by BOTH
        # plugin packages (lcm2-pg ships one too, and host boot leaves it
        # bound to lcm2-pg's file). `from _selfimport import anchor` there-
        # fore silently bound lcm2-pg's loader: anchor("plugin_entry")
        # resolved lcm2-pg/plugin_entry.py against lcm2's _HERE and
        # mnemo-pg2 registered lcm2's 15 lcm_* tools — zero mnemosyne_*
        # tools (F-1). Resolve OUR copy explicitly: package-qualified
        # import never touches the bare key (same discipline as
        # _sibling()/pgconn's own-FILE bind).
        anchor = _selfimport_module().anchor
        pe = anchor("plugin_entry")
        return pe.register(ctx, engine_factory=engine_factory)
    finally:
        _disarm()
        # 1. purge keys our exec CREATED that bind to OUR files
        for k in set(_sys.modules) - set(saved_mods):
            try:
                if _ours_bound(k) or k.startswith(("_mnemo2_impl_", "_lcm2_pg",
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
