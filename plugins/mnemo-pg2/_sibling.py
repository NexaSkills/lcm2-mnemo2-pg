"""Late (post-register) sibling binding — the single accessor.

memory_provider.py's provider object is LONG-LIVED: the host drives it
well after plugins/mnemo-pg2/__init__.py's register() finally-block has
byte-restored sys.modules (every bare sibling key back to whatever the
host/lcm2-pg held). A plain `import <name>` at call time would then
bind the WRONG module (lcm2-pg's pgconn under bare 'pgconn' — the
'connect() got an unexpected keyword argument kind' failure class: our
engine calls connect(kind=...) which only OUR pgconn accepts).

sibling(name) resolves OUR file-anchored module with three routes, in
order, each publishing ONLY under keys we own:

  1. sys.modules[name] when the existing binding resolves into OUR dir
     (the very module register()'s anchor published — identity, not a
     copy);
  2. the package-qualified import '<pkg>.<name>' when this accessor
     itself loaded as part of a package (publishes only under the
     package namespace);
  3. anchor(name) via OUR _selfimport (file-anchored; package-qualified
     / namespaced key resolution — never the shared bare '_selfimport'
     key, F-1's root-cause discipline).

Identical semantics to __init__._sibling (which cannot be imported
here without re-running the package __init__ — the double-exec hazard
this accessor exists to avoid); the anchor registry/identity tables are
shared by FILE, so all routes yield the SAME module objects.
"""
from __future__ import annotations

import importlib
import importlib.util as _ilu
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))


def _ours_bound(name: str) -> bool:
    m = sys.modules.get(name)
    f = getattr(m, "__file__", None)
    if not isinstance(f, str):
        return False
    try:
        return os.path.commonpath(
            (os.path.realpath(os.path.dirname(f)),
             os.path.realpath(_HERE))) == os.path.realpath(_HERE)
    except ValueError:  # pragma: no cover
        return False


def _selfimport_module():
    """OUR _selfimport module by any non-bare route (mirrors
    __init__._selfimport_module: package-qualified, then namespaced
    by-file; NEVER the bare '_selfimport' key a sibling plugin owns)."""
    pkg = __package__ or ""
    if pkg:
        try:
            return importlib.import_module(f"{pkg}._selfimport")
        except Exception:
            pass
    full = "_mnemo2_impl__selfimport"
    mod = sys.modules.get(full)
    if mod is not None and _ours_bound(full):
        return mod
    spec = _ilu.spec_from_file_location(full, os.path.join(_HERE, "_selfimport.py"))
    if spec is None or spec.loader is None:  # pragma: no cover
        raise ImportError("mnemo-pg2: _selfimport.py missing")
    mod = _ilu.module_from_spec(spec)
    sys.modules[full] = mod
    try:
        spec.loader.exec_module(mod)
    except BaseException:
        sys.modules.pop(full, None)
        raise
    return mod


def sibling(name: str):
    m = sys.modules.get(name)
    if m is not None and _ours_bound(name):
        return m
    pkg = __package__ or ""
    if pkg:
        try:
            return importlib.import_module(f"{pkg}.{name}")
        except Exception:
            pass
    return _selfimport_module().anchor(name)
