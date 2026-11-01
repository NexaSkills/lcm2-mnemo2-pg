"""/lcm slash command for lcm2-pg: status | inspect | grep <q> | doctor."""
from __future__ import annotations

import json


def _anchor_late(name):
    import plugin_entry as _pe  # F-2: post-register host-origin calls;
    return _pe._pkg_init()._anchor(name)  # the F1 purge frees the bare


_anchor = _anchor_late  # '_selfimport' key — plugin_entry resolves
                        # OUR package by file, never by bare name
# Resolved lazily in handle(): anchoring 'tools' at module IMPORT time
# publishes a bare name (under the plugin's package that is harmless, but
# the host module-namespace loader may exec this file with a package name
# that makes anchor() publish BARE 'tools' — stranding the host's real
# `import tools.<sub>` for the whole session: 'tools' is not a package).
_T = []


def _tools():
    if not _T:
        _T.append(_anchor("tools"))
    return _T[0]


def handle(raw: str, engine) -> str:
    parts = (raw or "").strip().split(None, 1)
    sub, rest = (parts + ["", ""])[:2]
    if sub in ("", "status"):
        return _tools().lcm_status({}, engine=engine)
    if sub == "inspect":
        return _tools().lcm_inspect({}, engine=engine)
    if sub == "doctor":
        return _tools().lcm_doctor({}, engine=engine)
    if sub == "grep":
        return _tools().lcm_grep({"query": rest}, engine=engine)
    return json.dumps({"usage": "/lcm [status|inspect|doctor|grep <q>]",
                       "implementation": "lcm2-pg/native"})


def _pkg_init():
    """OUR package __init__ module by FILE (the bare `__init__` name is
    freed by the F1 purge and belongs to the host; F-2 late-call
    resolver). Shares the namespaced key '_lcm2_pg_pkg' with every other
    copy: one shared package instance process-wide."""
    import importlib.util as _ilu
    import os as _os
    import sys as _s
    here = _os.path.dirname(_os.path.abspath(__file__))
    init = _os.path.realpath(_os.path.join(here, "__init__.py"))
    mod = _s.modules.get("_lcm2_pg_pkg")
    if mod is not None and getattr(mod, "__file__", None) and \
            _os.path.realpath(getattr(mod, "__file__", "") or "") == init:
        return mod
    for key, m in list(_s.modules.items()):
        f = getattr(m, "__file__", None) if m is not None else None
        if f and _os.path.realpath(f) == init and \
                callable(getattr(m, "_anchor", None)):
            _s.modules["_lcm2_pg_pkg"] = m
            return m
    spec = _ilu.spec_from_file_location(
        "_lcm2_pg_pkg", init, submodule_search_locations=[here])
    mod = _ilu.module_from_spec(spec)
    _s.modules["_lcm2_pg_pkg"] = mod
    try:
        spec.loader.exec_module(mod)
    except Exception:
        _s.modules.pop("_lcm2_pg_pkg", None)
        raise
    return mod
