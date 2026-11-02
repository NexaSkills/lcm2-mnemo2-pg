"""Install-time late-binding immunity (boot-order independent).

Two lazy resolutions can bind the WRONG module after register()'s
byte-exact restore (or before it, when the provider slot answers
FIRST): plugin_entry's module-global `_engine` and engine's module-
global `pgconn`. Both files import the name at MODULE TOP (`import
engine`, `import pgconn`), and a top-level bare import from one of our
files resolves ONLY through: (1) the bare sys.modules key — restored to
whatever the host/lcm2-pg owns, or (2) meta_path finders. register()'s
_SelfImportFinder DISARMS after register(), and the general lane may
anchor plugin_entry outside any window (the provider slot pulling
plugin_entry in first, or an anchor() whose window serves lcm2's
files). Both crash classes are one mechanism:

  * 'connect() got an unexpected keyword argument kind'  (engine.pgconn
    -> lcm2-pg's pgconn),
  * "module '_lcm2_impl_engine' has no attribute 'Engine'"
    (plugin_entry._engine -> lcm2-pg's engine module under the shared
    _lcm2_impl_ namespaced key — keys are namespaced, _HERE is per-copy,
    so OUR finder must sit BEFORE lcm2-pg's on sys.meta_path).

install() therefore:
  1. anchors OUR engine/pgconn/ddl/embed/plugin_entry under OUR names
     (via _sibling -> OUR _selfimport.anchor; identity with whatever
     the general lane published),
  2. installs a _SibFinder from OUR _selfimport (serves our dir's bare
     sibling imports for windows started outside register()),
  3. re-inserts it at meta_path position 0 (lcm2-pg's own finder is
     type-identical — identity beats type — and whichever finder answers
     a bare sibling import from an OUR-dir frame answers OUR file:
     the predicate is our dir, so lcm2's finder serving one for a
     lcm2-dir frame is untouched),
  4. pins the module-globals (setattr on OUR modules only; no
     sys.modules edit; the byte-exact restore invariant holds).

Idempotent: every step is a no-op once done. Called from
memory_provider.install() (both the general register() route and the
discovery-shim route run install()).
"""
from __future__ import annotations

import importlib.util as _ilu
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_LATE_FILES = ("engine", "pgconn", "ddl", "embed", "plugin_entry")


def _sib():
    try:
        from . import _sibling as _sib_mod  # package-qualified
        return getattr(_sib_mod, "sibling", None) or _sib_mod
    except Exception:
        here = os.path.dirname(os.path.abspath(__file__))
        sp = _ilu.spec_from_file_location(
            "_mnemo2_impl_latebind_sibling", os.path.join(here, "_sibling.py"))
        if sp is None or sp.loader is None:  # pragma: no cover
            return None
        m = _ilu.module_from_spec(sp)
        sys.modules[sp.name] = m
        try:
            sp.loader.exec_module(m)
            return m.sibling
        finally:
            sys.modules.pop(sp.name, None)


def _selfimport():
    try:
        from . import _selfimport as _si  # package-qualified, no bare key
        return _si
    except Exception:
        sp = _ilu.spec_from_file_location(
            "_mnemo2_impl_latebind_selfimport",
            os.path.join(_HERE, "_selfimport.py"))
        if sp is None or sp.loader is None:  # pragma: no cover
            return None
        m = _ilu.module_from_spec(sp)
        sys.modules[sp.name] = m
        try:
            sp.loader.exec_module(m)
            return m
        finally:
            sys.modules.pop(sp.name, None)


def install_serve_only() -> None:
    """Anchor OUR late-imported files and front-load OUR sibling finder
    so bare imports from our files (outside register()'s window) land on
    our files. No sys.path/sys.modules mutations."""
    si = _selfimport()
    if si is None:  # pragma: no cover
        return
    sib = _sib()
    if sib is None:  # pragma: no cover
        return
    for name in _LATE_FILES:
        try:
            sib(name)  # anchor via _sibling's routes (identity)
        except Exception:
            pass
    # OUR _SibFinder instance, front position: an OUR-dir frame's bare
    # import must not reach lcm2-pg's identical-type finder first. The
    # instance lives under a namespaced key (module attribute survives
    # register()'s purge; the sys.modules entry is re-set defensively —
    # it is OUR namespaced key, never a bare/host key).
    finder = getattr(si, "_latebind_sibfinder", None)
    if finder is None:
        try:
            finder = si._SibFinder()
        except Exception:  # pragma: no cover  (older _selfimport API)
            return
        si._latebind_sibfinder = finder
    sys.modules.setdefault("_mnemo2_impl_latebind_sibfinder", finder)
    for i, f in enumerate(sys.meta_path):
        if f is finder:
            if i:
                del sys.meta_path[i]
            break
    else:
        sys.meta_path.insert(0, finder)


def pin_engine() -> bool:
    """Bind OUR module-globals to the file-anchored modules. Idempotent;
    setattr on OUR modules only.

    SELF-HEALING by design: every late code path that touches engine
    (the provider class's prefetch/tool handlers, plugin_entry's
    dispatch/schema builders) calls _pin() FIRST, so the pin holds
    regardless of boot order or which lane anchored plugin_entry."""
    try:
        sib = _sib()
        if sib is None:  # pragma: no cover
            return False
        eng = sib("engine")
        if getattr(eng, "Engine", None) is None:
            return False  # pragma: no cover  (wrong file bound: refuse)
        pg = sys.modules.get("_mnemo2_impl_pgconn")
        if pg is None or getattr(pg, "connect", None) is None:
            pg = sib("pgconn")
        _sig = __import__("inspect").signature(pg.connect).parameters
        if "kind" not in _sig:
            return False  # wrong file bound: refuse to pin a foreign pgconn
        if getattr(eng, "pgconn", None) is not pg:
            setattr(eng, "pgconn", pg)
        # plugin_entry: OUR file-anchored copy. Route 3 (anchor by file
        # under OUR _selfimport) rather than _sibling route 2, because
        # _sibling's package-qualified fallback can hand back the copy
        # anchored under the SHARED key by another plugin's finder.
        pe = _anchor_pe(sib)
        if pe is None:  # pragma: no cover
            return False
        setattr(pe, "_engine", eng)
        return True
    except Exception:
        return False


def _anchor_pe(sib):
    """plugin_entry anchored to OUR file under OUR namespaced keys."""
    here = os.path.dirname(os.path.abspath(__file__))
    for k in ("_mnemo2_impl_plugin_entry",):
        m = sys.modules.get(k)
        f = getattr(m, "__file__", None)
        if isinstance(f, str) and os.path.realpath(f) == os.path.realpath(
                os.path.join(here, "plugin_entry.py")):
            return m
    si = _selfimport()
    if si is not None:
        try:
            return si.anchor("plugin_entry")
        except Exception:
            pass
    return sib("plugin_entry")
