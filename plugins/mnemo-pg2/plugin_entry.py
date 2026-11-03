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
import sys
import threading

import engine as _engine
import pgconn as _pgconn

TOOLSET = "memory"  # vendor parity (spec §2)

# The 42 wildcard names. SELF-CONTAINMENT (F-1): the authoritative list
# ships INSIDE the plugin dir (vendor_tools.txt, byte-identical to the
# repo's tests/mnemo2_vendor_tools_wildcard.txt — a test pins it), so a
# deployed plugin (profile/global plugin dirs, no repo checkout) always
# finds it. The repo tests-dir path stays as a fallback for checkouts.
_TOOLS_FILE_LOCAL = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "vendor_tools.txt")
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
    env = _envget("MNEMO2_TOOLS")
    if env:  # explicit list wins (probe escape hatch)
        return sorted(n.strip() for n in env.split(",") if n.strip())
    names = []
    for path in (_TOOLS_FILE_LOCAL, _WILDCARD_FILE):
        try:
            with open(path, "r", encoding="utf-8") as fh:
                names = sorted(n.strip() for n in fh if n.strip())
        except OSError:
            continue
        if len(names) >= 42:
            return names
        break
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
    """The vendor boundary: JSON kwargs in, JSON string out.

    Host contract (tools/registry.py::_normalize_handler_result): a
    handler's return must be a STRING (or the multimodal envelope) —
    a bare dict is rejected with tool_result_contract. So every path
    here returns json.dumps(...). Unknown/missing methods and any
    exception surface as an error dict — never an exception into the
    host (no fallback paths anywhere, R3)."""
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
        # P3L fix-close (option b): envbind.py is OUT of product code
        # (its only consumer was the 3rd-party callsite that never
        # materialised). Handlers registered by register() carry the
        # agent's register-time resolution (dsn/schema/shared_schema) as
        # a __binding__ marker; _dispatch installs it through
        # _envbind_ctx around the Engine call, so a rival agent's env
        # churn cannot redirect this agent's writes mid-call (r8
        # misplacement class). __binding__ is stripped before the Engine
        # call. A binding-less dispatch resolves per-call from env, as
        # before.
        _binding = None
        if isinstance(args, dict):
            _binding = args.get("__binding__")
        fn = getattr(eng, meth, None)
        if fn is None or not callable(fn):
            return json.dumps({"ok": False, "error": f"unknown tool: {tool_name}"})
        kwargs = dict(args or {})
        kwargs.pop("__engine__", None)
        kwargs.pop("__binding__", None)
        # handler may be called positionally ({...}) or with **kwargs by
        # the host; either way 'args' arrives as a dict here.
        if list(kwargs) == ["args"] and isinstance(kwargs["args"], dict):
            kwargs = kwargs["args"]
        # The **kwargs host path may deliver the marker too (handler
        # called with keyword args): pop it before the Engine call — it
        # is not an Engine kwarg.
        if "__binding__" in kwargs:
            _binding = kwargs.pop("__binding__") or _binding
        with _envbind_ctx(_binding):
            out = fn(**kwargs)
        if isinstance(out, str):
            return out
        if isinstance(out, dict):
            return json.dumps(out, default=str)
        return json.dumps({"ok": True, "result": out}, default=str)
    except TypeError as e:
        return json.dumps({"ok": False, "error": f"bad arguments for {tool_name}: {e}"})
    except Exception as e:  # surface, never swallow, never fall back (R3)
        return json.dumps({"ok": False, "error": f"{type(e).__name__}: {e}"})


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

class _envbind_ctx:
    """Self-contained per-agent env binding (P3L fix-close: the binding
    lives HERE, inside product code — no separate module file; its cell is
    located by a namespaced sys.modules key so pgconn can consult the
    SAME cell without importing this file). Layer the agent's
    register-time resolution (dsn/schema/shared_schema -> env names) over
    os.environ for the duration of one dispatch/hook.
    Engine resolves its defaults through pgconn's env funnel at connect
    time, which consults the same cell, so a rival agent's env churn can
    never redirect this agent's writes mid-call (r8 misplacement class).
    Module-global override cell located by sys.modules KEY (shared by
    every copy of this file in the interpreter, same discipline as
    _selfimport's registries)."""

    __slots__ = ("_layer",)

    _KEY = "_mnemo2_envbind_registry_ours"
    _TLS = threading.local()

    @classmethod
    def _stack(cls):
        # THREAD-LOCAL stack (P3L2): the earlier design kept one
        # process-global cell slot, so two agents dispatching on
        # different threads clobbered each other's layer — thread B read
        # thread A's binding (cross-agent schema misrouting under
        # concurrent register/dispatch, r8 class). A per-thread stack is
        # immune to that race by construction; nesting within a thread
        # still restores correctly via push/pop discipline.
        st = getattr(cls._TLS, "stack", None)
        if st is None:
            st = []
            cls._TLS.stack = st
        return st

    @classmethod
    def _cell(cls):
        # Registry module in sys.modules under _KEY: exposes top_layer()
        # (THIS thread's innermost layer or None) for readers like
        # pgconn._envget that must not import this file.
        reg = sys.modules.get(cls._KEY)
        if reg is None:
            import types as _t
            reg = _t.ModuleType(cls._KEY)
            setattr(reg, "top_layer",
                    lambda: (cls._stack() or [None])[-1])
            sys.modules[cls._KEY] = reg
        return reg

    def __init__(self, binding):
        _envbind_ctx._cell()  # forces the registry module into existence
        # Layer over os.environ AT ENTRY (late reads consult the layer
        # first and fall through, so ambient churn stays visible where a
        # binding names nothing — no stale full-env freeze).
        layer = dict(os.environ)
        # B2 fix1: the bind layer must NOT shadow the host's context-local
        # home (router scopes routed turns via CONTEXTVAR, os.environ
        # untouched). A dict copy always carries the LAUNCH HERMES_HOME,
        # which the fixed pgconn._live_home consults as fallback and
        # host-funnel-less readers read directly; drop it when it equals
        # the process/launch value so the funnel's override wins. Pinning
        # an explicit non-launch home via a binding stays untouched.
        if "HERMES_HOME" in layer:
            try:
                import hermes_constants as _hc
                if _hc.get_hermes_home_override() is None and (
                        os.environ.get("HERMES_HOME", "").strip()
                        == layer["HERMES_HOME"]):
                    del layer["HERMES_HOME"]
            except Exception:  # pragma: no cover - no Hermes present
                pass
        if binding:
            if binding.get("dsn"):
                layer["MNEMO2_PG_DSN"] = binding["dsn"]
            if binding.get("schema"):
                layer["MNEMO2_SCHEMA"] = binding["schema"]
            if binding.get("shared_schema"):
                layer["MNEMO2_SHARED_SCHEMA"] = binding["shared_schema"]
        self._layer = layer

    def __enter__(self):
        self._stack().append(self._layer)
        return self

    def __exit__(self, *exc):
        st = self._stack()
        # pop OUR layer even if a nested call misbalanced the stack
        if st and st[-1] is self._layer:
            st.pop()
        elif self._layer in st:
            st.remove(self._layer)
        return False


def _envget(name, default=None):
    """Env read honoring the innermost _envbind_ctx layer on THIS thread
    (falls through to the ambient os.environ). The binding lives on a
    thread-local stack (_mnemo2_envbind_registry_ours); the legacy
    envbind.py key _mnemo2_impl_envbind must NOT be reused here.
    plugin_entry never imports pgconn at cell level, so there is no
    import cycle on either load route."""
    stack = _envbind_ctx._stack()
    if stack:
        v = stack[-1].get(name, _MISSING)
        if v is not _MISSING:
            return v
    return os.environ.get(name, default)


_MISSING = object()


def _scoped(fn, binding):
    """Run fn under a dispatch binding (thread-local env override)."""
    with _envbind_ctx(binding):
        return fn()


def _on_session_start(binding=None, **kw):
    """Bootstrap the profile schema (lazy DDL through the plugin's own
    connect) + warm the pool. No-ops safely when PG is unreachable — the
    tools error loudly per-call instead (R3)."""
    try:
        def _go():
            with _pgconn.connect(None, None, ddl=True) as cx:
                cx.query_scalar("select 1")
        _scoped(_go, binding)
    except Exception:
        pass
    return None


def _pre_llm_call(binding=None, **kw):
    """Render the injected memory block (vendor "Mnemosyne Memory"-style):
    top global recalls for the last user message. Env-gated off with
    MNEMO2_PREFETCH=0. Returns {'context': str} or None (host contract)."""
    if _envget("MNEMO2_PREFETCH", "1") == "0":
        return None
    msg = kw.get("user_message") or kw.get("message") or ""
    if not isinstance(msg, str) or not msg.strip():
        return None
    try:
        res = _scoped(lambda: _engine.Engine().recall(msg, limit=3), binding)
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


def _post_tool_call(tool_name="", arguments=None, result=None,
                    binding=None, **kw):
    """Audit row for write tools into audit_log (spec §6). Fire-and-forget:
    auditing must never break a turn."""
    if tool_name not in _WRITE_TOOLS:
        return None
    try:
        def _go():
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
        _scoped(_go, binding)
    except Exception:
        pass
    return None


def register(ctx, engine_factory=None):
    """Toolset "memory", 42 tools, 3 hooks (spec §2/§3/§6).

    P3L: the per-agent resolution (dsn, private schema, shared schema)
    is captured from the env ONCE HERE and shipped to every dispatch as
    a thread-local binding — env churn by a rival agent (another
    register() or a pin window) can no longer redirect this agent's
    writes mid-call.
    """
    names = _wildcard_names()
    # P3L2: resolve EXPLICITLY through the env funnel (_envget = binding
    # layer if one is active on this thread, else ambient env). NOTE:
    # pgconn.default_dsn()/agent_schema()/shared_schema() read os.environ
    # DIRECTLY by design (they are the funnel's bottom rung); calling
    # them here would blind the register-time capture to an active
    # dispatch binding (e.g. a hook-internal re-register), which is the
    # misrouting class this lane exists to kill.
    try:
        dsn = _envget("MNEMO2_PG_DSN") or _pgconn.default_dsn()
        schema = _envget("MNEMO2_SCHEMA") or _pgconn.agent_schema()
        shared = _envget("MNEMO2_SHARED_SCHEMA", "shared")
        binding = {"dsn": dsn, "schema": schema, "shared_schema": shared}
        _pgconn._SCHEMA_RE.match(schema)  # invalid names => env-path fallback
        _pgconn._SCHEMA_RE.match(shared)
    except Exception:  # invalid env names: dispatch falls back to env path
        binding = {}
    for name in names:
        handler = (lambda n: (lambda args=None, _b=binding, **kw:
                              _dispatch(n, args if args is not None
                                        else {**kw, "__binding__": _b})))(name)
        ctx.register_tool(name=name, toolset=TOOLSET,
                          schema=_schema_for(name), handler=handler,
                          description=f"mnemo-pg2 vendor-parity tool {name}")

    def _hook(fn):
        """Bind the agent's resolution into the hook kwargs (host passes
        **kw only; an explicit param on our hooks receives it)."""
        if not binding:
            return fn

        def wrapped(**kw):
            kw.setdefault("binding", binding)
            return fn(**kw)
        return wrapped

    ctx.register_hook("on_session_start", _hook(_on_session_start))
    ctx.register_hook("pre_llm_call", _hook(_pre_llm_call))
    ctx.register_hook("post_tool_call", _hook(_post_tool_call))
    return {"tools": len(names), "hooks": 3}
