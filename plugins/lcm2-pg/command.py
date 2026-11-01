"""/lcm slash command for lcm2-pg: status | inspect | grep <q> | doctor."""
from __future__ import annotations

import json

from _selfimport import anchor as _anchor  # file-anchored: never the host's
T = _anchor("tools")                    # `tools` package if one pre-binds it


def handle(raw: str, engine) -> str:
    parts = (raw or "").strip().split(None, 1)
    sub, rest = (parts + ["", ""])[:2]
    if sub in ("", "status"):
        return T.lcm_status({}, engine=engine)
    if sub == "inspect":
        return T.lcm_inspect({}, engine=engine)
    if sub == "doctor":
        return T.lcm_doctor({}, engine=engine)
    if sub == "grep":
        return T.lcm_grep({"query": rest}, engine=engine)
    return json.dumps({"usage": "/lcm [status|inspect|doctor|grep <q>]",
                       "implementation": "lcm2-pg/native"})
