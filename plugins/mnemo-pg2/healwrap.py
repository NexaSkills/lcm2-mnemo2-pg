#!/usr/bin/env python3
"""Self-heal wrapper install (idempotent, import-time safe).

Wrap the PUBLIC entries of the two OUR-file copies that exist per boot
lane (memory_provider's provider class + plugin_entry's module-level
schema/dispatch helpers) so each call re-asserts the late-bind pins
(ENGINE_PGCONN/_ENGINE globals). The wrappers never change behavior:
they call latebind.pin_engine() (cheap idempotent setattrs) and then
delegate to the original function on the CALLER'S OWN module, so the
general lane's 42 tool handlers (closures over ITS plugin_entry copy)
and the provider lane's closures both heal whichever copy they live on.

No-op when latebind cannot prove both files (refuses foreign bindings).
"""
from __future__ import annotations

import os
import sys

_ENTRY_NAMES = {
    # module stem -> public callables to heal-wrap
    "plugin_entry": ("_schema_for", "_dispatch", "_render_block"),
    "memory_provider": (),  # provider class methods wrapped via class
}


def _our_copies():
    """Every module in sys.modules whose file is OUR plugin_entry.py or
    memory_provider.py (the two lanes load the SAME files under
    different keys — heal ALL copies)."""
    here = os.path.dirname(os.path.abspath(__file__))
    out = []
    for stem in ("plugin_entry", "memory_provider"):
        want = os.path.realpath(os.path.join(here, stem + ".py"))
        for k, m in list(sys.modules.items()):
            f = getattr(m, "__file__", None)
            if isinstance(f, str) and os.path.realpath(f) == want:
                out.append((stem, k, m))
    return out


def install_wrappers() -> int:
    """Heal-wrap the public entries on every OUR-file copy. Returns the
    number of wrappers installed (0 = nothing to do / refuse)."""
    import functools
    try:
        from . import latebind as _lb
    except Exception:
        try:
            import importlib.util as _ilu
            here = os.path.dirname(os.path.abspath(__file__))
            sp = _ilu.spec_from_file_location(
                "_mnemo2_impl_latebind_file",
                os.path.join(here, "latebind.py"))
            if sp is None or sp.loader is None:  # pragma: no cover
                return 0
            _lb = _ilu.module_from_spec(sp)
            sys.modules[sp.name] = _lb
            try:
                sp.loader.exec_module(_lb)
            finally:
                # keep the module alive under a namespaced key (the
                # wrappers reference it forever; register()'s purge
                # must not strand it)
                sys.modules.setdefault("_mnemo2_impl_latebind_file", _lb)
                sys.modules.pop(sp.name, None)
    if not _lb.pin_engine():
        return 0  # refuse: cannot prove our files
    n = 0
    for stem, key, mod in _our_copies():
        for fname in _ENTRY_NAMES.get(stem, ()):
            fn = getattr(mod, fname, None)
            if fn is None or getattr(fn, "_mnemo2_healed", False):
                continue

            def make(orig):
                @functools.wraps(orig)
                def healed(*a, **k):
                    _lb.pin_engine()
                    return orig(*a, **k)
                healed._mnemo2_healed = True
                return healed

            setattr(mod, fname, make(fn))
            n += 1
        # provider class: wrap the methods the HOST calls late
        prov_mod = mod if stem == "memory_provider" else None
        if prov_mod is not None:
            pub = sys.modules.get("_hermes_memory_providers.mnemosyne")
            prov = getattr(pub, "provider", None)
            cls = type(prov) if prov is not None else None
            for mname in ("handle_tool_call", "get_tool_schemas", "prefetch",
                          "is_available", "initialize", "system_prompt_block"):
                fn = getattr(cls, mname, None)
                if fn is None or getattr(fn, "_mnemo2_healed", False):
                    continue
                setattr(cls, mname, make(fn))
                n += 1
    return n
