"""lcm2-pg — PG-native Lossless Context Management (Phase 2+).

Hermes plugin entry: register(ctx) wires the context engine, 15 tools,
hooks, and the /lcm command. All internals live in plugin_entry.py and the
sibling modules (pgconn, ddl, engine, tools, hooks, command) — zero embedded-DB code
by construction (R3).
"""
from __future__ import annotations

import os as _os
import sys as _sys

# The plugin dir must resolve OUR sibling modules (ddl, engine, ...) even if
# a sibling plugin shadowed those names earlier in sys.path / sys.modules.
_HERE = _os.path.dirname(_os.path.abspath(__file__))
if _HERE not in _sys.path:
    _sys.path.insert(0, _HERE)
_here_key = _os.path.realpath(_HERE)
for _n, _m in list(_sys.modules.items()):
    _f = getattr(_m, "__file__", None)
    if _f and _os.path.dirname(_os.path.realpath(_f)) == _here_key \
            and _m is not _sys.modules.get(__name__):
        del _sys.modules[_n]  # re-import fresh from this directory

try:  # normal Hermes host import (package context)
    from .plugin_entry import register  # noqa: F401
except ImportError:  # flat sys.path import (tests add the plugin dir)
    from plugin_entry import register  # noqa: F401

__version__ = "0.2.0"
