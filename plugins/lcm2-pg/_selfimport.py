"""File-anchored sibling imports for the lcm2-pg plugin modules.

The plugin's modules import each other bare (`import tools`, `import
schemas`, ...) which only resolves while this directory owns those names
on sys.path/sys.modules. The HOST repo root also owns bare names — most
importantly a `tools` PACKAGE (host-root tools/__init__.py) that wins
whenever a host-side import runs after us (the context-engine loader, for
instance, imports host modules before ours). A bare `import tools` made
later would then silently bind the host's package and every tool dispatch
would "work" against the wrong module (error: Unknown context engine
tool).

anchor('tools', 'tools') -> our tools.py module, by FILE, always: it
resolves from __file__ (never sys.path/sys.modules), registers under both
the bare and package-qualified names so later bare imports hit
sys.modules first, and restores any foreign module it had to evict.
"""
from __future__ import annotations

import importlib.util
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_PKG = (__package__ or "").split(".")[0]


def _mine(mod) -> bool:
    f = getattr(mod, "__file__", None)
    return isinstance(f, str) and os.path.dirname(
        os.path.realpath(f)) == os.path.realpath(_HERE)


def anchor(name: str, filename: str | None = None):
    """Import <this dir>/<filename or name>.py by file and register it as
    `name` (plus `<pkg>.<name>`), returning the module."""
    filename = filename or name
    path = os.path.join(_HERE, filename + ".py")
    for candidate in (name, f"{_PKG}.{name}" if _PKG else None):
        if candidate and candidate in sys.modules and _mine(sys.modules[candidate]):
            return sys.modules[candidate]
    if not os.path.isfile(path):
        raise ImportError(f"lcm2-pg: {filename}.py not found in {_HERE}")
    full = f"_lcm2_impl_{name}"
    mod = sys.modules.get(full)
    if mod is None or not _mine(mod):
        spec = importlib.util.spec_from_file_location(full, path)
        if spec is None or spec.loader is None:
            raise ImportError(f"lcm2-pg: cannot build spec for {path}")
        # the target module's OWN bare sibling imports (tools -> engine ->
        # pgconn...) must resolve to our directory too: prepend our dir
        # for the duration and restore sys.path afterwards.
        sp = _HERE
        had = sp in sys.path
        if not had:
            sys.path.insert(0, sp)
        try:
            mod = importlib.util.module_from_spec(spec)
            sys.modules[full] = mod
            spec.loader.exec_module(mod)
        finally:
            if not had:
                try:
                    sys.path.remove(sp)
                except ValueError:
                    pass
    # publish under the bare + package names (evicting foreign holders;
    # they are restored by release() when the importing frame exits)
    names = [name] + ([f"{_PKG}.{name}"] if _PKG else [])
    evicted = {}
    for n in names:
        cur = sys.modules.get(n)
        if cur is not None and not _mine(cur):
            evicted[n] = sys.modules.pop(n)
        sys.modules[n] = mod
    if evicted:
        mod.__dict__["_lcm2_evicted"] = evicted
    return mod


def release(name: str) -> None:
    """Undo anchor()'s publication for `name`: drop our registrations and
    restore whatever foreign module was evicted (host code keeps its
    module objects either way)."""
    mod = sys.modules.get(name)
    evicted = getattr(mod, "_lcm2_evicted", {}) if mod else {}
    for n in [name] + ([f"{_PKG}.{name}"] if _PKG else []):
        if sys.modules.get(n) is mod:
            del sys.modules[n]
    for n, foreign in evicted.items():
        sys.modules.setdefault(n, foreign)
    if mod is not None:
        mod.__dict__.pop("_lcm2_evicted", None)
