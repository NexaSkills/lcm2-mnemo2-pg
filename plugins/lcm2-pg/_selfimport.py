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
# Exec-window depth, module-global (list cell: shared by EVERY copy of
# this file in the interpreter — the _lcm2_impl_, package-qualified and
# namespaced-alias loads must agree on ONE counter). anchor() increments
# it around every exec_module; __init__._SelfImportFinder._depth_cell()
# is THE authoritative cell. Locate by sys.modules KEY only — never
# `import` (any import re-enters the finders and would recurse).
_DEPTH_KEY = "_lcm2_depth_registry_ours"


def _depth_cell():
    import types as _t
    c = sys.modules.get(_DEPTH_KEY)
    if c is None:
        c = _t.ModuleType(_DEPTH_KEY)
        setattr(c, "CELL", [0])
        sys.modules[_DEPTH_KEY] = c
    return getattr(c, "CELL")


_ANCHOR_DEPTH = _depth_cell()


def _anchor_here_real():
    """Realpath of THIS copy's dir — stamped on every _SibFinder anchor()
    installs, checked in find_spec so an instance belonging to another
    loader copy's window can never serve files off the shared counter."""
    try:
        return os.path.realpath(_HERE)
    except Exception:  # pragma: no cover
        return _HERE
# Bare names the HOST repo root legitimately owns as packages (tools,
# engine, pgconn, ...). anchor() may publish OUR module under them ONLY
# while OUR frames are importing (restore evicts them again); leaving
# one published after anchor() returns is what strands the host's later
# `import tools.<sub>` ('tools' is not a package — F1 boot crash).
HOST_BARE = {"tools", "engine", "pgconn", "schemas", "command", "hooks",
             "ddl", "rollup_periods"}
# Bare names the HOST ROOT FLAT owns (files <root>/<name>.py — the
# interpreter itself: os/io/sys). anchor() may NEVER publish under these:
# the import system re-imports them from arbitrary frames all session and
# would bind our flat file (ModuleNotFoundError: 'tools' is not a package,
# the F1 crash class).
NEVER_PUBLISH = {"os", "io", "sys", "types", "json", "time", "logging"}
# Module-global depth of anchor() exec windows (ALL threads). Imports
# fired from inside our exec_module — worker threads spawned mid-exec,
# deferred calls, nested anchors — carry no "our" frame once they drift
# onto stdlib codepaths, so the frame-stack origin gate misses them.
# This counter covers those: while it is > 0 we serve OUR names; frame
# stacks are only consulted when it is 0 (late routes, no exec running).
_ANCHOR_DEPTH = [0]


class _SibFinder:
    """Serve OUR bare sibling imports from OUR dir WITHOUT sys.path.

    Installed for the duration of an anchor() exec only. Gated (F1
    flip): we serve ONLY while an anchor() exec is running
    (_ANCHOR_DEPTH > 0, covers every import our exec itself triggers,
    on any thread) or while the importing stack contains a frame from
    OUR dir (late anchor()-less calls). importlib walks meta_path
    identically for a HOST thread's `import tools.<sub>` (parent
    'tools' resolves through find_spec first), so any weaker gate lets
    a concurrent host import bind our flat file and strand it ('tools'
    is not a package) — the boot crash. Any other caller returns None
    and the normal path finders serve it.
    """

    def find_spec(self, fullname, path=None, target=None):
        if "." in fullname:
            return None
        if fullname not in _NAMES:
            return None
        # Counter gate: an anchor() exec window (any thread) authorizes
        # serving our file — but only if THIS instance is the one anchor()
        # installed for its window (a stale instance from an older/other
        # copy's window must not serve our files off a foreign counter).
        # Frame-stack origin is consulted only when NO window is open.
        if _ANCHOR_DEPTH[0] > 0:
            if getattr(self, "_anchor_here", None) != _anchor_here_real():
                return None
        elif not _origin_is_ours():
            return None  # host-origin import: never hijack it (F1)
        import importlib.util
        f = os.path.join(_HERE, fullname + ".py")
        if not os.path.isfile(f):
            return None
        return importlib.util.spec_from_file_location(fullname, f)


def _origin_is_ours():
    """True if any frame on the current import stack belongs to a module
    whose file is in OUR plugin dir (i.e. WE triggered this import).

    F-1 (P2CLOSE): importlib bootstrap frames are SKIPPED — `_bootstrap` /
    `_bootstrap_external` report __file__ inside the interpreter's own lib
    dir, which under a symlinked plugin layout can EQUAL the dir these
    helpers realpath() (the profile dir resolves through to repo files, but
    the frames themselves belong to the import machinery, not to us). An
    ordinary host import chain walking meta_path then looked "ours" and was
    served our flat file — the residual boot race.
    """
    try:
        here = os.path.realpath(_HERE)
    except Exception:
        return False
    try:
        fr = sys._getframe(1)
    except Exception:
        return False
    while fr is not None:
        fname = getattr(getattr(fr, "f_code", None), "co_filename", "")
        if str(fname).startswith(("<frozen ",)) or str(fname).split("/")[-1].startswith(
                ("_bootstrap", "_frozen_importlib")):
            fr = fr.f_back
            continue
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


def _host_pkg_owner(name):
    """File (module) the PROCESS would bind for a fresh bare `import <name>`,
    via the meta-path finders alone (no sys.path import cache). If that is a
    PACKAGE (has __path__, e.g. the host's tools/ package), the bare name is
    host-owned as a namespace: publishing our flat file under it strands
    `import <name>.<sub>` ('tools' is not a package — the F-1/F-2 boot
    crash class). anchor() must then serve us WITHOUT publishing."""
    import importlib.machinery as _im
    for finder in sys.meta_path:
        if isinstance(finder, (_SibFinder, _im.FrozenImporter)):
            continue
        try:
            spec = finder.find_spec(name)
        except Exception:
            continue
        if spec is not None:
            return spec
    return None


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
        # Second _SibFinder instance already in meta_path for OUR dir (a
        # second loader copy of this file)? Its find_spec consults THE
        # shared _ANCHOR_DEPTH cell, so reuse it instead of stacking a
        # duplicate. An instance anchored to a DIFFERENT dir is never
        # trusted inside our window (its find_spec checks _anchor_here).
        here_real = os.path.realpath(_HERE)
        inserted = True
        for f in list(sys.meta_path):
            if type(f).__name__ == "_SibFinder" and \
                    getattr(f, "_anchor_here", None) == here_real:
                finder = f
                inserted = False
                break
        if inserted:
            try:
                finder._anchor_here = here_real
            except Exception:
                pass
            sys.meta_path.insert(0, finder)
        _ANCHOR_DEPTH[0] += 1  # gate: imports from this exec (any thread)
        try:
            mod = importlib.util.module_from_spec(spec)
            sys.modules[full] = mod
            spec.loader.exec_module(mod)
        finally:
            _ANCHOR_DEPTH[0] -= 1
            if inserted:
                try:
                    sys.meta_path.remove(finder)
                except ValueError:
                    pass
    # publish under the bare + package names (evicting foreign holders;
    # they are restored by release() when the importing frame exits).
    # F-1: NEVER publish under a bare name the process resolves to a host
    # PACKAGE (tools/, engine/, ...): a flat file under that key breaks
    # the host's concurrent `import tools.<sub>` ('tools' is not a package)
    # for as long as the publication lives. Our code path is the returned
    # module object itself; the package-qualified key stays published so
    # later `import <pkg>.<name>` still resolves into OUR file.
    _ANCHORED.add(name)
    evicted = {}
    # F-1 FLIP (P2FLIP): NEVER publish under a bare name the HOST owns as
    # a package. The old "evict for the exec duration, restore after"
    # dance is what strands the host: importlib resolves `import
    # tools.<sub>` through the parent binding in sys.modules regardless
    # of WHICH thread/frame asked, so any host-thread submodule import
    # landing inside our exec window sees our flat file as 'tools' and
    # caches a poisoned module inside the host's own import lock
    # ('tools' is not a package — the boot crash no frame-origin gate
    # can fix). Under the flip the bare key NEVER changes hands for a
    # host-package name: we serve our code via the _lcm2_impl_<name>
    # key (already published above) + the package-qualified alias.
    # Bare publish survives ONLY when the process resolves the name to a
    # non-package (no host package owns it: standalone/test world) or
    # when _PKG gives us a package-qualified key to hide behind.
    publish = []
    if _PKG:
        publish.append(f"{_PKG}.{name}")
    if name.startswith("_"):
        publish.append(name)  # underscore-private: no host/stdlib importer
    else:
        try:
            _sp = _host_pkg_owner(name)
            host_pkg = _sp is not None and _sp.submodule_search_locations is not None
        except Exception:
            host_pkg = True  # undecided owner: treat as host-owned (safe side)
        if not host_pkg:
            publish.append(name)  # standalone world: the bare name is ours
        elif sys.modules.get(name) is not None and _mine(sys.modules[name]):
            publish.append(name)  # we already legitimately own the binding
    for n in dict.fromkeys(publish):
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
