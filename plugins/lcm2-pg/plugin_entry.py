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

    # tools (vendor names, vendor-boundary contracts)
    register_tool = getattr(ctx, "register_tool", None)
    tool_names = [n for n in dir(T) if n.startswith("lcm_")]
    if callable(register_tool):
        for name in sorted(tool_names):
            fn = getattr(T, name)
            if not callable(fn):
                continue
            try:
                register_tool(
                    name=name,
                    toolset="context_engine",
                    schema={"name": name, "description": f"lcm2-pg {name}",
                            "parameters": {"type": "object", "properties": {}}},
                    handler=_wrap(fn, get_engine),
                    description=f"lcm2-pg native {name}",
                )
            except Exception as exc:
                logger.warning("lcm2-pg tool %s registration failed: %s", name, exc)

    # context engine (compression path for the host)
    register_context_engine = getattr(ctx, "register_context_engine", None)
    if callable(register_context_engine):
        register_context_engine(get_engine())

    # hooks
    register_hook = getattr(ctx, "register_hook", None)
    if callable(register_hook):
        register_hook("pre_llm_call", hooks.make_pre_llm_call(get_engine))
        register_hook("subagent_start",
                      hooks.make_subagent_recorder(get_engine, "subagent_start"))
        register_hook("subagent_stop",
                      hooks.make_subagent_recorder(get_engine, "subagent_stop"))

    # /lcm slash command (env-gated like vendor)
    register_command = getattr(ctx, "register_command", None)
    if callable(register_command) and os.environ.get(
            "LCM_ENABLE_SLASH_COMMAND", "1") != "0":
        import command
        register_command("lcm", lambda raw: command.handle(raw, get_engine()),
                         description="lcm2-pg status/inspect/grep")

    register_skill = getattr(ctx, "register_skill", None)
    if callable(register_skill):
        try:
            register_skill("lcm2-pg", os.path.dirname(__file__) + "/skills",
                           description="Native PG lossless context (lcm2-pg).")
        except Exception:
            pass


def _wrap(fn, get_engine):
    def handler(args, **kwargs):
        if not kwargs.get("engine"):
            # the host calls handlers with args only; the engine always
            # comes from the register()-bound factory (never a fresh,
            # differently-scoped LCM2Engine)
            kwargs["engine"] = get_engine()
        try:
            return fn(args or {}, **kwargs)
        except Exception as exc:  # well-formed JSON error, never a crash
            return json.dumps({"error": f"{type(exc).__name__}: {exc}",
                               "implementation": "lcm2-pg/native"})
    return handler
