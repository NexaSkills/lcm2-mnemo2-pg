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
        import __init__ as _pkg  # only ever OUR package __init__
        return _pkg._anchor


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
