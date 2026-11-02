"""F-1 late-binding pin (provider lifetime).

Memory-provider lifecycle calls land HERE (a bound method of
_MnemoPg2Provider) — there are NO of-module frames in the import chain,
so _SelfImportFinder's origin predicate and _ServeOnlyFinder both
correctly decline, and a bare lazy `import engine` (plugin_entry.py's
module line) would re-run sys.modules discovery: after register()'s
byte-exact restore the bare `engine` key belongs to lcm2-pg's engine
PACKAGE (or the host root's), whose connect() has no kind= kwarg — the
live 'unexpected keyword argument kind' crash class.

pin_engine() binds plugin_entry's module-global `_engine` to OUR
file-anchored engine module directly (identity: _sibling's first route
returns the very module register()'s anchor published; on the
provider-lifetime timeline anchor()'s sys.modules restore is already
done and never runs again, so rebinding a module-global is stable).
Idempotent; called from memory_provider.install().
"""
from __future__ import annotations

import os
import sys


def pin_engine() -> bool:
    try:
        try:
            from . import _sibling as _sib_mod  # package-qualified
            sib = getattr(_sib_mod, "sibling", None) or _sib_mod
        except Exception:
            import importlib.util as _ilu
            here = os.path.dirname(os.path.abspath(__file__))
            sp = _ilu.spec_from_file_location(
                "_mnemo2_impl_latebind_sibling",
                os.path.join(here, "_sibling.py"))
            if sp is None or sp.loader is None:  # pragma: no cover
                return False
            m = _ilu.module_from_spec(sp)
            sys.modules[sp.name] = m
            try:
                sp.loader.exec_module(m)
                sib = m.sibling
            finally:
                sys.modules.pop(sp.name, None)
        eng = sib("engine")
        # THE PIN (connect-kind class): engine's module-global `pgconn`
        # is what every engine call resolves at RUN time (engine.py line
        # `import pgconn` ran under register()'s window, and on the
        # provider-lifetime timeline a late re-import would hit the
        # bare key lcm2-pg owns). Bind it to OUR pgconn by file — the
        # same object the anchor published (_sibling route 1), so no
        # second copy exists anywhere.
        pg = sib("pgconn")
        _sig = __import__("inspect").signature(pg.connect).parameters
        if getattr(pg, "connect", None) is None or "kind" not in _sig:
            return False  # pragma: no cover  (wrong file bound: refuse)
        if getattr(eng, "pgconn", None) is not pg:
            setattr(eng, "pgconn", pg)
        pe = sib("plugin_entry")
        if getattr(eng, "Engine", None) is None:  # pragma: no cover
            return False
        if getattr(pe, "_engine", None) is not eng:
            setattr(pe, "_engine", eng)
        return True
    except Exception:
        return False
