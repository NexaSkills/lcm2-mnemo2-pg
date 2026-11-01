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
