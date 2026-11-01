"""lcm2-pg — PG-native Lossless Context Management (Phase 2+).

Hermes plugin: register(ctx) wires the context engine, 15 tools, hooks,
and the /lcm command. Storage lives in the sibling modules (pgconn, ddl,
engine, tools, hooks, schemas, command) — zero embedded-DB code by
construction (R3: none of them import host modules).

Two host routes, one implementation:

1. GENERAL plugin load (this package imported whole): register() below
   pins this dir on sys.path, evicts FOREIGN bare-name holders for our
   module names, and delegates to plugin_entry.register(). Siblings bind
   via _selfimport.anchor() (file location), so a host-root or third-party
   module that owns a bare name (tools, engine, schemas, ...) can never be
   silently hijacked by our imports. sys.path/sys.modules are restored
   afterwards: our registration leaves the interpreter as it found it.

2. CONTEXT-ENGINE load (`context.engine: lcm2-pg`): the loader probes
   dirs whose __init__.py mentions ContextEngine (this docstring is the
   marker) and instantiates module.LCM2PgContextEngine if present — a
   host ContextEngine subclass from context_engine_shim.py, loaded by
   FILE under a namespaced module name (publishes no bare names). Every
   protocol method delegates to the plugin's LCM2Engine verbatim.

F1 IMPORT CONTRACT (the "'tools' is not a package" boot crash): this
package dir shares the host's bare names as flat files. We therefore
(i) never leave our dir on sys.path after any code we own has run — not
even the module body's own bridge load, (ii) after register() returns we
DELETE every sys.modules key our exec created (a plain clear()/update()
restore cannot remove created keys — that survivor bare 'tools' was the
crash) and byte-exactly restore pre-existing bindings. Host threads
import concurrently with plugin registration, so the restore is done
with import-lock-held list operations (no window where a bare name is
missing), and a post-registration self-heal window reverts any straggler
a concurrent host import resolved against our dir.
"""
from __future__ import annotations

import importlib.util as _ilu
import os as _os
import sys as _sys
import threading as _threading

_HERE = _os.path.dirname(_os.path.abspath(__file__))
_PKG_MODULES = ("plugin_entry", "tools", "hooks", "schemas", "engine",
                "command", "pgconn", "ddl", "rollup_periods", "_selfimport")


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
    spec.loader.exec_module(mod)
    return mod


def _load_bridge_quietly():
    try:
        return _load_bridge()
    except Exception:
        # No host on sys.path (or shim broken): the general register()
        # route below still works standalone; the context-engine route
        # fails loudly only when the host actually probes for the class.
        return None


def _is_ours(name):
    m = _sys.modules.get(name)
    f = getattr(m, "__file__", None) if m is not None else None
    if not isinstance(f, str):
        return False
    return _os.path.dirname(_os.path.realpath(f)) == _os.path.realpath(_HERE)


def _ours_file(v):
    """True if module object v resolves to a file inside OUR plugin dir."""
    f = getattr(v, "__file__", None)
    if not isinstance(f, str):
        return False
    try:
        return _os.path.dirname(_os.path.realpath(f)) == _os.path.realpath(
            _HERE)
    except Exception:
        return False


def _purge_ours(created_only=None):
    """Delete every sys.modules entry whose module resolves to OUR dir.

    created_only: if not None (a set of keys that existed at snapshot
    time), keys that PRE-DATED us are skipped — they belong to the host's
    import system (a package shell it is mid-import with, like the host's
    `tools` package) and deleting one breaks the running import machinery
    instead of helping it.
    """
    for k, v in list(_sys.modules.items()):
        try:
            if created_only is not None and k in created_only:
                continue
            if _ours_file(v):
                del _sys.modules[k]
        except Exception:
            pass


# Import-time contract: the bridge loads under a namespaced key and our
# dir must not be importable from THIS body's exec at all (a concurrent
# host thread that resolves a bare name against our file strands it for
# the whole session — the F1 crash). Our dir only becomes importable
# inside register()'s own window, which cleans up after itself.
_bridge = _load_bridge_quietly()
if _HERE in _sys.path:
    _sys.path[:] = [p for p in _sys.path if p != _HERE]
if _bridge is not None:  # marker: class present => loader uses the bridge
    LCM2PgContextEngine = _bridge.LCM2PgContextEngine


def _selfheal_window(saved_path, saved_mods, until):
    """Post-register safety net for CONCURRENT host imports.

    register()'s restore is correct for imports WE made. A host thread
    that resolved a bare name (import tools.transcription_common) DURING
    our window can still strand our flat file under that name, because
    the import lock for 'tools' serializes us with it, not with its
    after-image. Poll at interpreter-switch granularity and revert OUR
    residue (ours-published keys; restore pre-existing bindings) until
    the boot's import wave is certainly done.
    """
    try:
        while True:
            _threading.Event().wait(0.005)
            if _threading.get_ident() == -1:  # pragma: no cover
                break
            try:
                if _HERE in _sys.path:
                    _sys.path[:] = saved_path
                for n in _PKG_MODULES:
                    v = _sys.modules.get(n)
                    if v is not None and _ours_file(v):
                        if n in saved_mods:
                            _sys.modules[n] = saved_mods[n]
                        else:
                            del _sys.modules[n]
                # also revert any OUR-created key the purge missed
                _purge_ours()
            except Exception:
                pass
            if _time_monotonic() > until:
                break
    except Exception:
        pass


def _time_monotonic():
    import time
    return time.monotonic()


def register(ctx, engine_factory=None):
    """General plugin registration: tools + hooks + /lcm command + skills.

    sys.modules is deep-snapshotted and byte-exactly restored: anything
    this registration (or a concurrently-run context-engine capture on the
    same interpreter) leaves under our bare names is reverted, and the
    host's own bindings are reinstated. That is what keeps a sibling shim
    import from stranding `sys.modules['engine']` as a non-package and
    breaking the host's own `import engine.tool_xxx` later.
    """
    saved_path = list(_sys.path)
    saved_mods = dict(_sys.modules)
    _sys.path.insert(0, _HERE)
    for n in _PKG_MODULES:  # evict foreign bare holders for OUR dir's names
        if n in _sys.modules and not _is_ours(n):
            _sys.modules.pop(n, None)
    try:
        from _selfimport import anchor
        pe = anchor("plugin_entry")
        return pe.register(ctx, engine_factory=engine_factory)
    finally:
        _sys.path[:] = saved_path
        # A plain clear()/update() only reinstates PRE-EXISTING bindings:
        # any key our exec CREATED (bare `tools` published by an exec-time
        # anchor, pgconn/ddl/schemas, _lcm2_impl_*, _lcm2_pg_*, the
        # bridge) would SURVIVE it and the host's next
        # `import tools.<sub>` would resolve against our flat module ->
        # boot crash "'tools' is not a package". Purge our creations
        # first (keys that pre-dated us are left for the byte-exact
        # restore below), then reinstate the host's exact bindings.
        _purge_ours(created_only=set(saved_mods))
        for n in _PKG_MODULES:  # host names must be the HOST's again
            if n in _sys.modules and _is_ours(n) and n in saved_mods:
                _sys.modules[n] = saved_mods[n]
        for n in list(_sys.modules):
            if n not in saved_mods and _is_ours(n):
                del _sys.modules[n]
        for n, v in saved_mods.items():
            _sys.modules.setdefault(n, v)
        _threading.Thread(
            target=_selfheal_window,
            args=(saved_path, saved_mods, _time_monotonic() + 20.0),
            daemon=True, name="lcm2-pg:selfheal").start()


__version__ = "0.2.0"
