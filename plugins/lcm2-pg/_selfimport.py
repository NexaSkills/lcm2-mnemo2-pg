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
_ANCHORED: set = set()  # names ever anchor()'d in this interpreter


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
    _ANCHORED.add(name)
    evicted = {}
    for n in names:
        cur = sys.modules.get(n)
        if cur is not None and not _mine(cur):
            evicted[n] = sys.modules.pop(n)
        sys.modules[n] = mod
    if evicted:
        mod.__dict__["_lcm2_evicted"] = evicted
    return mod


def release(name: str, publish: bool = False) -> None:
    """Undo anchor()'s publication for `name`: drop our registrations and
    restore whatever foreign module was evicted (host code keeps its
    module objects either way).

    publish=True is the at-exit variant: the bare name still resolves to
    OUR module (a plain import inside our module bodies stays correct)
    and no foreign module is force-restored; the eviction bookkeeping is
    just cleared. Without it the host's evicted bindings come back.
    """
    mod = sys.modules.get(name)
    if mod is None or not _mine(mod):
        mod = sys.modules.get(f"_lcm2_impl_{name}")
    evicted = getattr(mod, "_lcm2_evicted", {}) if mod else {}
    if publish:
        # Keep our bare registrations in place; nothing to restore.
        if mod is not None:
            mod.__dict__.pop("_lcm2_evicted", None)
        return
    for n in [name] + ([f"{_PKG}.{name}"] if _PKG else []):
        if sys.modules.get(n) is mod:
            del sys.modules[n]
    for n, foreign in evicted.items():
        sys.modules.setdefault(n, foreign)
    if mod is not None:
        mod.__dict__.pop("_lcm2_evicted", None)


def release_all() -> None:
    """Undo anchor()'s bare/package publication for EVERY name this
    interpreter ever anchor()'d (`_ANCHORED`, not the current dict, so
    names already cleaned by an inner release() are still visited). The
    plugin's own module bodies anchor bare siblings at exec time
    (`command.py` anchors `tools` on import), so relying on one call site
    to tidy up is not enough: a leaked bare `tools` breaks the HOST's
    `import tools.<submodule>` for the whole session ('tools' is not a
    package). No-op-safe: release() only deletes a binding that is still
    OUR module and only reinstates what anchor() itself evicted.
    """
    for n in sorted(_ANCHORED):
        try:
            release(n)
        except Exception:  # pragma: no cover - best-effort cleanup
            pass
    # Belt for names published during exec by a route that never reaches
    # release() (e.g. a plain `import tools` inside our module bodies
    # while _HERE is on sys.path): purge any binding that still points at
    # OUR file, whatever key it hides under. Foreign keys are untouched.
    for k, v in list(sys.modules.items()):
        try:
            if _mine(v):
                del sys.modules[k]
        except Exception:  # pragma: no cover
            pass
