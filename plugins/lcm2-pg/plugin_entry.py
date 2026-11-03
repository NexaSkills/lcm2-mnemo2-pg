"""lcm2-pg: PG-native Lossless Context Management (clean-room redesign of
hermes-lcm). Zero embedded-DB code by construction: all storage is psycopg against
lcm2db, one private schema lcm2_<agent> per agent.

Register entry point (Hermes plugin contract):
    register(ctx, engine_factory=None)
"""
from __future__ import annotations

import importlib.util as _ilu
import json
import logging
import os
import sys as _sys

logger = logging.getLogger("lcm2-pg")

try:  # F-1 diagnostics (env-gated, no-op unless LCM2_DIAG=1)
    import _diag_watch as _dw  # noqa

    _dw.start()
except Exception:
    pass

__version__ = "0.2.0"


def _pkg_init():
    """OUR package __init__ module object, by FILE under a namespaced key
    (never `import __init__` — the bare name resolves the HOST's module
    of that name; F-2 late-call resolver)."""
    here = os.path.dirname(os.path.abspath(__file__))
    init = os.path.realpath(os.path.join(here, "__init__.py"))
    for key, mod in list(_sys.modules.items()):
        # the package that already EXECUTED this exact file (host's
        # canonical import, or a previous resolver call) — reuse it.
        if key == "__main__" or mod is None or mod is _sys.modules.get("_lcm2_pg_pkg"):
            continue
        if getattr(mod, "__file__", None) and \
                os.path.realpath(getattr(mod, "__file__", "") or "") == init \
                and os.path.dirname(os.path.realpath(
                    getattr(mod, "__file__", "") or "")) == \
                os.path.realpath(here) and callable(getattr(mod, "_anchor", None)):
            return mod
    mod = _sys.modules.get("_lcm2_pg_pkg")
    if mod is not None and callable(getattr(mod, "_anchor", None)):
        return mod
    spec = _ilu.spec_from_file_location(
        "_lcm2_pg_pkg", init, submodule_search_locations=[here])
    if spec is None or spec.loader is None:  # pragma: no cover
        raise ImportError(f"lcm2-pg: package __init__ missing in {here}")
    mod = _ilu.module_from_spec(spec)
    _sys.modules["_lcm2_pg_pkg"] = mod
    try:
        spec.loader.exec_module(mod)
    except BaseException:  # never leave a half-built alias behind
        if _sys.modules.get("_lcm2_pg_pkg") is mod:
            del _sys.modules["_lcm2_pg_pkg"]
        raise
    return mod


def _anchor_late():
    """anchor() callable for LATE (post-register, host-origin) calls:
    resolves OUR _selfimport through the package, never the bare name
    the F1 purge frees (F-2 fix route b)."""
    return _pkg_init()._anchor


def _engine_factory_from_env():
    try:  # F-2: late (host-origin) call — the F1 purge deletes the bare
        from _selfimport import anchor
    except Exception:      # '_selfimport' key; OUR package __init__
        anchor = _anchor_late()  # resolves anchor() package-qualified
    return anchor("engine").LCM2Engine()


def _release_all():
    """release_all() for the end of register(): resolve _selfimport via a
    key that exists HERE (bare during the exec window; the package route
    survives the purge) — never a bare host-bound import."""
    try:
        from _selfimport import release_all
        return release_all
    except Exception:
        return _pkg_init()._selfimport_module().release_all


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

        def _slot_attempt():
            # Try the raw duck-typed engine FIRST: test/host fakes that record
            # it (ctx.context_engine) or accept anything get the real engine —
            # no hidden wrapper. BUT the stock host gate is warn-and-ignore
            # (returns None on isinstance failure, no exception), so an
            # exception-only fallback never fires and the slot silently stays
            # empty ('does not inherit from ContextEngine. Ignoring.'): check
            # isinstance BEFORE registering and fall back to the shim bridge,
            # which wraps the same engine in the ContextEngine subclass.
            try:
                from agent.context_engine import ContextEngine as _CE
                _bridge_cls = None
                try:
                    _here = os.path.dirname(os.path.abspath(__file__))
                    _ce_shim = _sys.modules.get("_lcm2_pg_bridge_ce_shim")
                    if _ce_shim is None or not getattr(_ce_shim, "LCM2PgContextEngine", None):
                        import importlib.util as _u
                        _sp = _u.spec_from_file_location(
                            "_lcm2_pg_bridge_ce_shim",
                            os.path.join(_here, "context_engine_shim.py"))
                        _md = _u.module_from_spec(_sp)
                        _sys.modules["_lcm2_pg_bridge_ce_shim"] = _md
                        try:
                            _sp.loader.exec_module(_md)
                        except Exception:
                            _sys.modules.pop("_lcm2_pg_bridge_ce_shim", None)
                            raise
                        _ce_shim = _md
                    _bridge_cls = _ce_shim.LCM2PgContextEngine
                except Exception as exc:
                    logger.debug("lcm2-pg bridge class unavailable: %s", exc)
                    _bridge_cls = None
                _e = factory() if _bridge_cls is None else _bridge_cls(
                    dsn=getattr(factory(), "dsn", None),
                    schema=getattr(factory(), "schema", None))
                # Prefer registering the factory's own engine (host fakes
                # assert identity), else the bridge wrapper (real subclass,
                # passes the host's isinstance gate). Register BOTH when the
                # fake/host slot is permissive: second call overwrites slot,
                # tools/hooks unaffected.
                raw_ok = isinstance(_e, _CE)
                register_context_engine(_e if raw_ok else factory())
                if not raw_ok:
                    # bridge failed to construct: try raw anyway (host may
                    # still accept it; warn-and-ignore is its problem, our
                    # tools/hooks work regardless)
                    try:
                        register_context_engine(factory())
                    except Exception:
                        pass
            except Exception:
                raw_ok = False
                try:
                    register_context_engine(factory())
                except Exception:
                    pass

        # X-1 engine-slot precedence: when BOTH lcm-pg (legacy) and this
        # plugin are installed, the winner is decided by the explicit rule
        # in engine_slot.py (CONTEXT_ENGINE marker > context.engine config
        # > LCM2_PG_DSN > :54331 probe > legacy default) — NOT by plugin
        # load order. Single-stack hosts: enforce_slot short-circuits and
        # _slot_attempt runs exactly as before (zero behavior change).
        # On a WIN where the rival already holds the slot, enforce_slot
        # retires the rival's lease and re-runs _slot_attempt; on a LOSS
        # it logs the ONE yield line and skips the doomed claim.
        try:
            _es = anchor("engine_slot")
        except Exception:
            _es = None
        if _es is None:
            _slot_attempt()
        else:
            _es.enforce_slot(ctx, "lcm2-pg", _slot_attempt, logger)

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
            # OUR command module (file-anchored), not the host registry's
            # binding: the SAME plugin is seen by both loaders and the
            # other loader's copy may have grabbed `/lcm` first (R3
            # isolation makes those distinct module objects).
            from hermes_cli.plugins import get_plugin_commands
            taken = "lcm" in (get_plugin_commands() or {})
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
