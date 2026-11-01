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
"""
from __future__ import annotations

import importlib.util as _ilu
import os as _os
import sys as _sys

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


_bridge = _load_bridge_quietly()
if _bridge is not None:  # marker: class present => loader uses the bridge
    LCM2PgContextEngine = _bridge.LCM2PgContextEngine


def _is_ours(name):
    m = _sys.modules.get(name)
    f = getattr(m, "__file__", None) if m is not None else None
    if not isinstance(f, str):
        return False
    return _os.path.dirname(_os.path.realpath(f)) == _os.path.realpath(_HERE)


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
        _sys.modules.clear()
        _sys.modules.update(saved_mods)  # byte-exact: host's view restored


__version__ = "0.2.0"
