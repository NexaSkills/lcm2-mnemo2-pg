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
    import engine
    return engine.LCM2Engine()


def register(ctx, engine_factory=None):
    """Plugin entry: context engine + 15 tools + hooks + /lcm command."""
    import tools as T
    import hooks

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
        import schemas as S
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

    # context engine (compression path for the host). The host gates this
    # on isinstance(agent.context_engine.ContextEngine); the plugin engine
    # is duck-typed and never imports host modules (R3), so on a stock host
    # this call warns-and-ignores — EXPECTED. The host-native engine path is
    # context.engine: lcm2-pg resolved through plugins/context_engine/lcm2-pg
    # (subclass shim reusing this engine's methods verbatim); tools reach the
    # model via that engine's get_tool_schemas()/handle_tool_call() and via
    # the registry registrations above. Keep the call: on hosts that accept
    # duck-typed engines it activates the engine directly.
    register_context_engine = getattr(ctx, "register_context_engine", None)
    if callable(register_context_engine):
        try:
            register_context_engine(get_engine())
        except Exception as exc:
            logger.debug("lcm2-pg register_context_engine skipped: %s", exc)

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
        import command
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
            import engine as _E
            ours = isinstance(eng, _E.LCM2Engine)
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
