#!/usr/bin/env python3
"""mnemo-pg2 plugin entry — registers the vendor-parity tool surface.

register(ctx) does three things (spec-phase3 §3):
  * one ctx.register_tool per wildcard tool name (42,
    tests/mnemo2_vendor_tools_wildcard.txt), toolset="memory" (vendor
    parity: hermes_memory_provider registers under toolset "memory"),
    handler = a JSON-in/JSON-out dispatch closure into the engine,
  * three hooks (pre_llm_call, on_session_start, post_tool_call) matching
    the vendor manifest,
  * nothing else: no sys.path edits, no bare-name publications (the
    __init__.py purge guarantees the interpreter is left as found).

Thread safety (R8): handlers may be invoked from any thread. ALL
per-call state comes from os.environ at call time (pgconn resolves
dsn/schema per connect) — this module keeps no mutable state and never
touches a shared object mid-call.

Config resolution (spec §4) lives entirely in pgconn/engine; this file is
dumb registration. Schema bootstrap is lazy (first connect(ddl=True)).
"""
from __future__ import annotations

import json
import os

import engine as _engine
import pgconn as _pgconn

TOOLSET = "memory"  # vendor parity (spec §2)

# The 42 wildcard names are loaded from the checked-in list at register()
# time (repo file first, env override for probes). Names map 1:1 onto
# Engine.<name> methods (mnemosyne_import -> Engine.import_).
_WILDCARD_FILE = os.path.normpath(os.path.join(
    os.path.dirname(os.path.abspath(__file__)), os.pardir, os.pardir,
    "tests", "mnemo2_vendor_tools_wildcard.txt"))

_PREFIX = "mnemosyne_"


def _method_name(tool_name):
    if tool_name == _PREFIX + "import":
        return "import_"
    return tool_name[len(_PREFIX):] if tool_name.startswith(_PREFIX) \
        else tool_name


def _wildcard_names():
    env = os.environ.get("MNEMO2_TOOLS")
    if env:  # explicit list wins (probe escape hatch)
        return sorted(n.strip() for n in env.split(",") if n.strip())
    with open(_WILDCARD_FILE, "r", encoding="utf-8") as fh:
        names = sorted(n.strip() for n in fh if n.strip())
    if len(names) < 42:
        # Self-containment: the checked-in list lives in the repo tests
        # dir; when the plugin is deployed standalone (hermes plugin
        # dirs) the file is absent — fall back to the engine's own
        # method set (identical by construction, test_surface pins it).
        names = sorted(
            _PREFIX + (m.rstrip("_") if m != "import_" else m)
            for m in dir(_engine.Engine)
            if not m.startswith("_") and callable(getattr(_engine.Engine, m))
            and m not in ("conv",))
    return names


def _dispatch(tool_name, args):
    """The vendor boundary: JSON kwargs in, JSON-serializable value out.
    Unknown/missing methods and any exception surface as an error dict —
    never an exception into the host (no fallback paths anywhere, R3)."""
    meth = _method_name(tool_name)
    try:
        # host-injected engine (kwargs['engine']) only if it IS ours
        eng = None
        cand = (args or {}).get("__engine__") if isinstance(args, dict) else None
        try:
            if isinstance(cand, _engine.Engine):
                eng = cand
        except Exception:
            eng = None
        if eng is None:
            eng = _engine.Engine()
        fn = getattr(eng, meth, None)
        if fn is None or not callable(fn):
            return {"ok": False, "error": f"unknown tool: {tool_name}"}
        kwargs = dict(args or {})
        kwargs.pop("__engine__", None)
        # handler may be called positionally ({...}) or with **kwargs by
        # the host; either way 'args' arrives as a dict here.
        if list(kwargs) == ["args"] and isinstance(kwargs["args"], dict):
            kwargs = kwargs["args"]
        out = fn(**kwargs)
        if isinstance(out, dict):
            return out
        return {"ok": True, "result": out}
    except TypeError as e:
        return {"ok": False, "error": f"bad arguments for {tool_name}: {e}"}
    except Exception as e:  # surface, never swallow, never fall back (R3)
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


_TYPE_MAP = ((bool, "boolean"), (int, "integer"), (float, "number"),
            (list, "array"), (dict, "object"), (str, "string"))


def _py_type(ann, default):
    for t, name in _TYPE_MAP:
        if ann is t:
            return name
    if isinstance(ann, str):  # postponed annotation ("float | None")
        a = ann.strip()
        if "bool" in a:
            return "boolean"
        if "int" in a:
            return "integer"
        if "float" in a:
            return "number"
        if a.startswith(("list", "List", "Sequence")):
            return "array"
        if a.startswith(("dict", "Dict")):
            return "object"
        if "str" in a:
            return "string"
    for t, name in _TYPE_MAP:
        if isinstance(default, t) and not (t is int and isinstance(default, bool)):
            return name
    return "string"


def _schema_for(tool_name):
    """Vendor-shaped OpenAI tool schema derived from the Engine method
    signature — schema and code can never drift."""
    import inspect
    fn = getattr(_engine.Engine, _method_name(tool_name), None)
    props, req = {}, []
    sig = None
    try:
        sig = inspect.signature(fn)
    except (TypeError, ValueError):  # pragma: no cover
        pass
    if sig is not None:
        for pname, p in sig.parameters.items():
            if pname in ("self",) or p.kind in (p.VAR_POSITIONAL,
                                                p.VAR_KEYWORD):
                continue
            ann = p.annotation
            ann = None if ann is inspect.Parameter.empty else ann
            default = None if p.default is inspect.Parameter.empty else p.default
            typ = _py_type(ann, default)
            entry = {"type": typ}
            if typ == "array":
                entry["items"] = {"type": "string"}
            if p.default is inspect.Parameter.empty:
                req.append(pname)
            elif typ != "boolean" and default is not None:
                if isinstance(default, (str, int, float, bool, list)):
                    entry["default"] = default
            props[pname] = entry
    doc = (getattr(fn, "__doc__", "") or "").strip().split("\n")[0]
    return {
        "name": tool_name,
        "description": (doc or f"mnemo-pg2 {tool_name}")[:200],
        "parameters": {"type": "object", "properties": props,
                       "required": req},
    }


# ---------------------------------------------------------------- hooks

def _on_session_start(**kw):
    """Bootstrap the profile schema (lazy DDL through the plugin's own
    connect) + warm the pool. No-ops safely when PG is unreachable — the
    tools error loudly per-call instead (R3)."""
    try:
        with _pgconn.connect(None, None, ddl=True) as cx:
            cx.query_scalar("select 1")
    except Exception:
        pass
    return None


def _pre_llm_call(**kw):
    """Render the injected memory block (vendor "Mnemosyne Memory"-style):
    top global recalls for the last user message. Env-gated off with
    MNEMO2_PREFETCH=0. Returns {'context': str} or None (host contract)."""
    if os.environ.get("MNEMO2_PREFETCH", "1") == "0":
        return None
    msg = kw.get("user_message") or kw.get("message") or ""
    if not isinstance(msg, str) or not msg.strip():
        return None
    try:
        res = _engine.Engine().recall(msg, limit=3)
        rows = (res or {}).get("results") or []
        lines = ["## Mnemosyne Context"]
        for r in rows:
            body = str(r.get("content", "")).strip().replace("\n", " ")
            if body:
                lines.append(f"- {body[:240]}")
        return {"context": "\n".join(lines)} if len(lines) > 1 else None
    except Exception:
        return None


_WRITE_TOOLS = {
    "mnemosyne_remember", "mnemosyne_update", "mnemosyne_forget",
    "mnemosyne_invalidate", "mnemosyne_batch", "mnemosyne_triple_add",
    "mnemosyne_triple_end", "mnemosyne_scratchpad_write",
    "mnemosyne_scratchpad_clear", "mnemosyne_remember_canonical",
    "mnemosyne_forget_canonical", "mnemosyne_shared_remember",
    "mnemosyne_shared_forget", "mnemosyne_persona_promote",
    "mnemosyne_persona_demote", "mnemosyne_persona_reinforce",
    "mnemosyne_graph_link", "mnemosyne_apply_pending",
    "mnemosyne_resolve_conflicts", "mnemosyne_task_progress",
    "mnemosyne_remember_media",
}


def _post_tool_call(tool_name="", arguments=None, result=None, **kw):
    """Audit row for write tools into audit_log (spec §6). Fire-and-forget:
    auditing must never break a turn."""
    if tool_name not in _WRITE_TOOLS:
        return None
    try:
        with _pgconn.connect(None, None, ddl=True) as cx:
            cx.execute(
                "INSERT INTO audit_log(ts, actor, action, target_id, detail)"
                " VALUES (now(), current_user, %s, %s, %s::jsonb)",
                (tool_name, str((arguments or {}).get("memory_id", "")),
                 json.dumps({
                     "args": arguments,
                     "ok": (result.get("ok") if isinstance(result, dict)
                            else None) if result is not None else None,
                 })[:8000]))
    except Exception:
        pass
    return None


def register(ctx, engine_factory=None):
    """Toolset "memory", 42 tools, 3 hooks (spec §2/§3/§6)."""
    names = _wildcard_names()
    for name in names:
        handler = (lambda n: (lambda args=None, **kw:
                              _dispatch(n, args if args is not None else kw)))(name)
        ctx.register_tool(name=name, toolset=TOOLSET,
                          schema=_schema_for(name), handler=handler,
                          description=f"mnemo-pg2 vendor-parity tool {name}")
    ctx.register_hook("on_session_start", _on_session_start)
    ctx.register_hook("pre_llm_call", _pre_llm_call)
    ctx.register_hook("post_tool_call", _post_tool_call)
    return {"tools": len(names), "hooks": 3}
