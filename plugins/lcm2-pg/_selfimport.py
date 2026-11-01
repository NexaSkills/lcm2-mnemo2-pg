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
# Bare names the HOST repo root legitimately owns as packages (tools,
# engine, pgconn, ...). anchor() may publish OUR module under them ONLY
# while OUR frames are importing (restore evicts them again); leaving
# one published after anchor() returns is what strands the host's later
# `import tools.<sub>` ('tools' is not a package — F1 boot crash).
HOST_BARE = {"tools", "engine", "pgconn", "schemas", "command", "hooks",
             "ddl", "rollup_periods"}


class _SibFinder:
    """Serve OUR bare sibling imports from OUR dir WITHOUT sys.path.

    Installed for the duration of an anchor() exec only. Origin-gated
    (F1): a MetaPathFinder serves EVERY import in the process, including
    a HOST thread's `import tools.<sub>` running concurrently with our
    exec window. Serving that host import our flat tools.py strands it
    ('tools' is not a package) — exactly the boot crash. So we only
    answer when the import ORIGINATES inside our plugin dir (a frame
    from this dir is on the importing stack); any other caller returns
    None and the normal path finders serve it.
    """

    def find_spec(self, fullname, path=None, target=None):
        if "." in fullname:
            return None
        if fullname not in _NAMES:
            return None
        if not _origin_is_ours():
            return None  # host-origin import: never hijack it (F1)
        import importlib.util
        f = os.path.join(_HERE, fullname + ".py")
        if not os.path.isfile(f):
            return None
        return importlib.util.spec_from_file_location(fullname, f)


def _origin_is_ours():
    """True if any frame on the current import stack belongs to a module
    whose file is in OUR plugin dir (i.e. WE triggered this import)."""
    try:
        here = os.path.realpath(_HERE)
    except Exception:
        return False
    try:
        fr = sys._getframe(1)
    except Exception:
        return False
    while fr is not None:
        fn = getattr(fr, "f_globals", {}).get("__file__", "")
        if isinstance(fn, str) and fn:
            try:
                if os.path.dirname(os.path.realpath(fn)) == here:
                    return True
            except Exception:
                pass
        fr = fr.f_back
    return False


_NAMES = {"plugin_entry", "tools", "hooks", "schemas", "engine", "command",
          "pgconn", "ddl", "rollup_periods", "_selfimport"}


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
        # The target module's OWN bare sibling imports (tools -> engine ->
        # pgconn...) must resolve to our files. NEVER insert _HERE into
        # the global sys.path: the host boots with concurrent import
        # threads and any host `import tools.<sub>` landing while our dir
        # is on sys.path binds OUR flat tools.py ('tools' is not a
        # package — F1 boot crash), no matter how briefly. Serve the
        # siblings through a transient meta-path finder instead (exact
        # names only; global sys.path is never touched).
        finder = _SibFinder()
        inserted = finder not in sys.meta_path
        if inserted:
            sys.meta_path.insert(0, finder)
        try:
            mod = importlib.util.module_from_spec(spec)
            sys.modules[full] = mod
            spec.loader.exec_module(mod)
        finally:
            if inserted:
                try:
                    sys.meta_path.remove(finder)
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
