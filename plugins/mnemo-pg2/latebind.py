"""Provider-side late-binding pins.

The provider object and plugin_entry's handlers outlive
plugins/mnemo-pg2/__init__.py's register(), whose finally-block
byte-restores sys.modules. Two lazy resolutions can then bind the WRONG
module — the live crash classes:

  * engine's module-global `pgconn` re-resolved at connect time onto
    lcm2-pg's pgconn (its connect() has no kind= kwarg: 'connect() got
    an unexpected keyword argument kind');
  * plugin_entry's module-global `_engine` on lcm2-pg's engine module
    ('module _lcm2_impl_engine has no attribute Engine' — the shim/
    provider route anchoring OUR plugin_entry late, outside register()'s
    _SibFinder window).

pin_engine() binds both module-globals to OUR file-anchored modules
through _sibling (route 1 = the SAME objects register()'s anchor
published; identity, never a copy). setattr on OUR modules only —
no sys.modules edit, so register()'s byte-exact restore stays intact.
Anchor() during a live register() window restores the bare keys at
exit, so the pin is correct no matter WHEN it runs (install(), the
discovery shim's register(), or both). Idempotent.
"""
from __future__ import annotations

import importlib.util as _ilu
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
# OUR lazily-bound files. Anchoring these up-front makes every late
# window that imports them run under anchor()'s _SibFinder window
# (our files, regardless of which host frame starts the import chain).
_NAMES = ("engine", "pgconn", "ddl", "embed")


def _anchor_late_files():
    for name in _NAMES:
        try:
            full = "_mnemo2_impl_" + name
            if sys.modules.get(full) is not None:
                continue
            spec = _ilu.spec_from_file_location(full, os.path.join(_HERE, name + ".py"))
            if spec is None or spec.loader is None:  # pragma: no cover
                continue
            mod = _ilu.module_from_spec(spec)
            sys.modules[full] = mod
            try:
                spec.loader.exec_module(mod)
            except BaseException:
                sys.modules.pop(full, None)
                raise
        except Exception:
            pass


class _ServeOnlyFinder:
    """Serve OUR <name>.py for bare import of `name` ONLY when the
    importer frame is one of OUR files (identical predicate and
    bootstrap-skip discipline as _selfimport._SibFinder /
    __init__._from_our_dir; host frames always get the normal answer).
    Publishes NOTHING into sys.modules — the byte-exact restore of
    register() stays intact."""

    def __init__(self, name):
        self._name = name

    def find_spec(self, fullname, path=None, target=None):
        if fullname != self._name:
            return None
        try:
            here = os.path.realpath(_HERE)
        except Exception:  # pragma: no cover
            here = _HERE
        try:
            fr = sys._getframe(1)
        except Exception:  # pragma: no cover
            return None
        while fr is not None:
            base = os.path.basename(str(getattr(fr.f_code, "co_filename", "")))
            if base.startswith(("_bootstrap", "_frozen_importlib")):
                fr = fr.f_back
                continue
            fn = getattr(fr.f_globals, "__file__", "")
            if isinstance(fn, str) and fn:
                try:
                    if os.path.dirname(os.path.realpath(fn)) == here:
                        full = "_mnemo2_impl_" + self._name
                        mod = sys.modules.get(full)
                        if mod is None:  # not anchored yet
                            return None
                        return getattr(mod, "__spec__", None)
                except Exception:
                    pass
            fr = fr.f_back
        return None


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


def install_serve_only() -> None:
    """Idempotent: anchor our lazily-imported files + install one
    frame-gated finder per name (no sys.path/sys.modules edits)."""
    _anchor_late_files()
    for name in _NAMES:
        if any(type(f).__name__ == "_ServeOnlyFinder"
               and getattr(f, "_name", None) == name
               for f in sys.meta_path):
            continue
        sys.meta_path.insert(0, _ServeOnlyFinder(name))


def pin_engine() -> bool:
    """Bind OUR lazily-resolved module-globals (plugin_entry._engine,
    engine.pgconn) to the file-anchored modules. Boot-order independent
    and idempotent; setattr on OUR modules only."""
    try:
        sib = _sib()
        if sib is None:  # pragma: no cover
            return False
        eng = sib("engine")
        pg = sib("pgconn")
        pe = sib("plugin_entry")
        _sig = __import__("inspect").signature(pg.connect).parameters
        if "kind" not in _sig:
            return False  # wrong file bound: refuse to pin a foreign pgconn
        if getattr(eng, "pgconn", None) is not pg:
            setattr(eng, "pgconn", pg)
        if getattr(getattr(pe, "_engine", None), "Engine", None) is None:
            setattr(pe, "_engine", eng)
        return True
    except Exception:
        return False
