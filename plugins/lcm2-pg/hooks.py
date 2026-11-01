"""lcm2-pg hooks: pre_llm_call ingestion+compression+recall guidance,
subagent_start/stop lineage rows."""
from __future__ import annotations

import json
import logging
import os

def _anchor_late():
    """anchor() for LATE (post-register, host-origin) calls: the F1 purge
    deletes the bare '_selfimport' key, so fall back to OUR package
    __init__'s resolver (package-qualified, survives the purge; F-2)."""
    try:
        from _selfimport import anchor
        return anchor
    except Exception:
                return _pkg_init()._anchor


try:  # file-anchored sibling (F1: a bare `import pgconn` under the
    from _selfimport import anchor as _anchor
    pgconn = _anchor("pgconn")  # context-engine module-namespace route
    _pgconn_bare = False       # would hijack the HOST's own pgconn for
except Exception:              # every later import in the process
    import pgconn              # last-resort (general route: our file wins)
    _pgconn_bare = True

def _pg():
    """pgconn module, resolved lazily if the exec-time anchor failed."""
    global pgconn, _pgconn_bare
    if _pgconn_bare:
        try:
            pgconn = _anchor_late()("pgconn")
            _pgconn_bare = False
        except Exception:
            pass
    return pgconn

logger = logging.getLogger("lcm2-pg")

GUIDANCE = (
    "lcm2-pg (native PG LCM) is active: every message is persisted in your "
    "private schema; summaries are DAG-folded. Search with lcm_grep/lcm_recall; "
    "drill with lcm_expand(store_id=/node_id=). Treat summaries as cues, not "
    "exact quotes."
)


def _session_id(payload: dict) -> str:
    return (payload.get("session_id")
            or os.environ.get("HERMES_SESSION_ID") or "default")


def make_pre_llm_call(engine_factory):
    def _hook(**payload):
        try:
            # F-2: route the WHOLE turn through the register()-bound
            # engine closure. The host's context-engine slot may hold a
            # module-identity DIFFERENT LCM2Engine (the CE-capture route);
            # an ambient env or an injected engine from that other module
            # would silently write elsewhere or fail. Same DSN/schema in
            # practice; identical results, one code path.
            e = engine_factory()
            sid = _session_id(payload)
            msg = payload.get("user_message") or payload.get("message") or ""
            if msg:
                e.ingest(sid, "user", str(msg))
            reply = payload.get("assistant_message") or payload.get("response")
            if reply:
                e.ingest(sid, "assistant", str(reply))
            e.maybe_compress(sid)
            return {"context_add": GUIDANCE}
        except Exception as exc:  # never break the host turn
            logger.warning("lcm2-pg pre_llm_call: %s", exc)
            return None
    return _hook


def make_subagent_recorder(engine_factory, kind: str):
    def _hook(**payload):
        try:
            e = engine_factory()
            with pgconn.connect(e.dsn, e.schema) as cx:
                cx.execute(
                    "insert into lcm_trajectory_transitions"
                    "(from_state, to_state, payload)"
                    " values (%s, %s, %s::jsonb)",
                    (kind + "_begin", kind + "_end",
                     json.dumps(payload.get("payload", payload), default=str)))
        except Exception as exc:
            logger.warning("lcm2-pg %s hook: %s", kind, exc)
    return _hook


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
