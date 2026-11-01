"""lcm2-pg: PG-native Lossless Context Management (clean-room redesign of
hermes-lcm). Zero embedded-DB code by construction: all storage is psycopg against
lcm2db, one private schema lcm2_<agent> per agent.

Register entry point (Hermes plugin contract):
    register(ctx, engine_factory=None)
"""
from __future__ import annotations

import json
import logging
import os

logger = logging.getLogger("lcm2-pg")

__version__ = "0.2.0"


def _engine_factory_from_env():
    try:  # F-2: late (host-origin) call — the F1 purge deletes the bare
        from _selfimport import anchor
    except Exception:      # '_selfimport' key; OUR package __init__
        import __init__ as _pkg  # resolves anchor() package-qualified
        anchor = _pkg._anchor
    return anchor("engine").LCM2Engine()


def _release_all():
    """release_all() for the end of register(): resolve _selfimport via a
    key that exists HERE (bare during the exec window; the namespaced
    alias survives the purge) — never a bare host-bound import."""
    try:
        from _selfimport import release_all
        return release_all
    except Exception:
        import __init__ as _pkg  # only ever OUR package __init__
        return _pkg._selfimport_module().release_all


def _pkg_init():
    """OUR package __init__ module object, resolved via __spec__ (never
    `import __init__` — the bare name resolves the HOST's module of that
    name, and sys.modules keys get purged after register())."""
    import importlib.util as _ilu
    import os as _os
    init = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)),
                         "__init__.py")
    m = getattr(__spec__, "parent", None)
    mod = __import__("sys").modules.get(m) if m else None
    if mod is not None and getattr(mod, "_lcm2_pkg_marker", None) == init:
        return mod
    spec = _ilu.spec_from_file_location(
        "_lcm2_pg_pkg", init, submodule_search_locations=[_os.path.dirname(init)])
    mod = _ilu.module_from_spec(spec)
    __import__("sys").modules["_lcm2_pg_pkg"] = mod
    spec.loader.exec_module(mod)
    mod._lcm2_pkg_marker = init
    return mod


def register(ctx, engine_factory=None):
    """Plugin entry: context engine + 15 tools + hooks + /lcm command."""
    from _selfimport import anchor
    T = anchor("tools")
    hooks = anchor("hooks")

    factory = engine_factory or _engine_factory_from_env
    _engine_holder = {}

    def get_engine():
        if "e" not in _engine_holder:
            _engine_holder["e"] = factory()
        return _engine_holder["e"]

    # tools (vendor names, vendor-boundary contracts). Schemas come from
    # tool_schemas.py (real input schemas, mirrors the vendor 15); the
    # manifest additionally declares provides_tools so the host surfaces
    # them in CLI/TUI sessions (F1).
    register_tool = getattr(ctx, "register_tool", None)
    if callable(register_tool):
        S = anchor("schemas")  # file-anchored, never the host's bare name
        for name in sorted(S.TOOLS):
            fn = getattr(T, name, None)
            if not callable(fn):
                continue
            schema = S.TOOLS[name]
            try:
                register_tool(
                    name=name,
                    toolset="context_engine",
                    schema=schema,
                    handler=_wrap(fn, get_engine),
                    description=schema.get("description", ""),
                )
            except Exception as exc:
                logger.warning("lcm2-pg tool %s registration failed: %s", name, exc)

    # context engine (compression path for the host). The host gates
    # register_context_engine on isinstance(agent.context_engine.ContextEngine)
    # and the plugin engine is duck-typed by design (R3: plugin modules never
    # import host modules). So register the ENGINE BRIDGE — the same
    # LCM2Engine wrapped by the ContextEngine subclass in
    # context_engine_shim.py — not the raw duck-typed object. The shim's
    # register() does exactly that; running it here reuses that single
    # definition instead of duplicating the wrapper. On hosts whose gate is
    # stricter or absent, the call is harmless (warn-and-ignore / accepted).
    register_context_engine = getattr(ctx, "register_context_engine", None)
    if callable(register_context_engine):
        # Try the raw duck-typed engine FIRST: test/host fakes that record
        # it (ctx.context_engine) or accept anything get the real engine —
        # no hidden wrapper. A strict isinstance gate (stock host) rejects it
        # with TypeError; only THEN fall back to the shim bridge, which
        # registers the same engine wrapped in the ContextEngine subclass.
        try:
            register_context_engine(factory())
        except Exception:
            try:
                from context_engine_shim import register as _bridge_register
                _bridge_register(ctx)
            except Exception as exc:
                logger.debug("lcm2-pg context engine bridge skipped: %s", exc)

    # hooks
    register_hook = getattr(ctx, "register_hook", None)
    if callable(register_hook):
        register_hook("pre_llm_call", hooks.make_pre_llm_call(get_engine))
        register_hook("subagent_start",
                      hooks.make_subagent_recorder(get_engine, "subagent_start"))
        register_hook("subagent_stop",
                      hooks.make_subagent_recorder(get_engine, "subagent_stop"))

    # /lcm slash command (env-gated like vendor). Guarded: the same plugin
    # is seen by BOTH loaders (general plugin + context-engine capture, F1
    # engine resolution lands on this dir) — whichever registers second
    # skips silently instead of warning about the already-owned command.
    register_command = getattr(ctx, "register_command", None)
    if callable(register_command) and os.environ.get(
            "LCM_ENABLE_SLASH_COMMAND", "1") != "0":
        command = anchor("command")
        try:
            from hermes_cli.commands import resolve_command
            taken = resolve_command("lcm") is not None
        except Exception:
            taken = False
        if not taken:
            try:
                register_command(
                    "lcm", lambda raw: command.handle(raw, get_engine()),
                    description="lcm2-pg status/inspect/grep")
            except Exception:
                pass

    register_skill = getattr(ctx, "register_skill", None)
    if callable(register_skill):
        try:
            register_skill("lcm2-pg", os.path.dirname(__file__) + "/skills",
                           description="Native PG lossless context (lcm2-pg).")
        except Exception:
            pass

    # Leave the interpreter AS WE FOUND IT: our modules stay reachable via
    # direct references (module globals and handler closures — all our
    # cross-imports are file-anchored), the bare names go back to exactly
    # what they were before register() ran. Publishing our modules under
    # host bare names is NOT safe: model_tools re-imports `tools` at
    # dispatch time and the host re-imports `engine.*` submodules — a file
    # module under either name breaks those imports for the whole session.
    try:
        _release_all()()
    except Exception:
        pass


def _wrap(fn, get_engine):
    def handler(args, **kwargs):
        # The host (tool_executor._resolve_engine) injects its activated
        # context-engine instance as engine=. That instance belongs to a
        # DIFFERENT loader (the context-engine capture path vs this general
        # plugin loader): when the injected object is not one of OUR
        # LCM2Engine instances (duck-typed bridge wrapper included), its
        # handle_tool_call would answer "Unknown context engine tool"
        # (different module identity, no shared dispatch). Use our own
        # register()-bound engine instead — same DSN, same schema (both
        # derive from HERMES_HOME), identical results.
        eng = kwargs.get("engine")
        try:
            from _selfimport import anchor as _a
            ours = isinstance(eng, _a("engine").LCM2Engine)
        except Exception:
            ours = False
        if not ours:
            kwargs["engine"] = get_engine()
        try:
            return fn(args or {}, **kwargs)
        except Exception as exc:  # well-formed JSON error, never a crash
            return json.dumps({"error": f"{type(exc).__name__}: {exc}",
                               "implementation": "lcm2-pg/native"})
    return handler
