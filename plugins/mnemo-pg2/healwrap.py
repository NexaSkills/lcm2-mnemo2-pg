"""Self-heal wrappers (idempotent) — F-1 late-bind immunity, final layer.

The general lane's 42 tool handlers are closures built during ITS
register() window; the provider lane's provider object is long-lived.
If a rival plugin's loader answers one of our files' top-level bare
imports outside our windows (the '_lcm2_impl_engine has no attribute
Engine' / 'connect() got unexpected kwarg kind' crash classes), the
WRONG module sits in a module-global of one of OUR module copies.

heal() re-asserts the pins (latebind.pin_engine: setattr of OUR
file-anchored modules onto plugin_entry._engine / engine.pgconn — OUR
globals only, no sys.modules edits, byte-exact restore intact) and is
wrapped around the PUBLIC entry points of every OUR-file copy present
in the interpreter (both plugin_entry copies, provider class methods),
so every host-driven call heals its own copy right before using it.

Refuses (no-op) whenever latebind cannot prove our files.
"""
from __future__ import annotations

import functools
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))


def _latebind():
    try:
        from . import latebind as _lb
        return _lb
    except Exception:
        pass
    lb = sys.modules.get("_mnemo2_impl_latebind_file")
    if lb is not None:
        return lb
    import importlib.util as _ilu
    sp = _ilu.spec_from_file_location(
        "_mnemo2_impl_latebind_file", os.path.join(_HERE, "latebind.py"))
    if sp is None or sp.loader is None:  # pragma: no cover
        return None
    m = _ilu.module_from_spec(sp)
    sys.modules["_mnemo2_impl_latebind_file"] = m
    try:
        sp.loader.exec_module(m)
    except BaseException:  # pragma: no cover
        sys.modules.pop("_mnemo2_impl_latebind_file", None)
        raise
    return m


def _our_copies(stem: str):
    want = os.path.realpath(os.path.join(_HERE, stem + ".py"))
    out = []
    for k, m in list(sys.modules.items()):
        f = getattr(m, "__file__", None)
        if isinstance(f, str) and os.path.realpath(f) == want:
            out.append(m)
    return out


def _wrap(obj, attr, lb):
    fn = getattr(obj, attr, None)
    if fn is None or getattr(fn, "_mnemo2_healed", False):
        return 0

    @functools.wraps(fn)
    def healed(*a, **k):
        lb.pin_engine()
        return fn(*a, **k)

    healed._mnemo2_healed = True
    setattr(obj, attr, healed)
    return 1


def heal() -> int:
    """Pin + heal-wrap. Idempotent; returns count of NEW wrappers."""
    lb = _latebind()
    if lb is None or not lb.pin_engine():
        return 0
    n = 0
    for mod in _our_copies("plugin_entry"):
        for fname in ("_schema_for", "_dispatch", "_render_block"):
            n += _wrap(mod, fname, lb)
    for mod in _our_copies("memory_provider"):
        pub = sys.modules.get("_hermes_memory_providers.mnemosyne")
        prov = getattr(pub, "provider", None)
        cls = type(prov) if prov is not None else None
        if cls is not None:
            for mname in ("handle_tool_call", "get_tool_schemas", "prefetch",
                          "is_available", "initialize",
                          "system_prompt_block"):
                n += _wrap(cls, mname, lb)
    return n
