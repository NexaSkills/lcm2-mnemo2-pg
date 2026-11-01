"""lcm2-pg context engine — sanctioned host-native bridge (F1 path 2).

``context.engine: lcm2-pg`` resolves to a directory whose __init__.py
mentions ContextEngine (loader heuristic): the bundled
``plugins/context_engine/lcm2-pg/`` layout (this file's home in the repo)
or ``$HERMES_HOME/plugins/lcm2-pg/`` via the profile symlink. The
context-engine loader calls ``register(ctx)`` here and captures the
engine via ``ctx.register_context_engine``.

The engine class subclasses the host's
``agent.context_engine.ContextEngine`` — satisfying the isinstance gate —
while reusing the REAL lcm2-pg plugin engine
(plugins/lcm2-pg/engine.py) VERBATIM through the MRO: every protocol
method (should_compress, compress, get_tool_schemas, handle_tool_call,
on_session_start, clone_for_agent, update_model, ...) is the plugin's.
No behavior lives here. The plugin itself never imports host modules
(R3 isolation); THIS file is the single sanctioned bridge, loaded by the
host's context-engine loader — importing ContextEngine at module scope is
what the dir heuristic and the subclass scan need, so it is top-level and
the whole module fails loudly (never silently) if there is no host.

Construction is lazy: no engine, connection pool, or DB touch until the
host actually activates the engine. If the plugin dir is unreachable,
instantiation raises and the host falls back to the built-in compressor
with an accurate warning — never a silent half-engine.
"""
from __future__ import annotations

import importlib.util
import os
import pathlib
import sys

from agent.context_engine import ContextEngine


def _load_plugin_engine_class() -> type:
    """Import the plugin's LCM2Engine by FILE location (namespaced module
    name so it can never collide with a bare `engine` already on
    sys.modules — the host repo root puts its own there). Candidate order:
    env override, this dir (profile-symlink layout), the sibling
    plugins/lcm2-pg of the repo checkout, then the global fallbacks."""
    here = pathlib.Path(__file__).resolve().parent
    cands = [
        os.environ.get("LCM2_PLUGIN_DIR"),
        str(here),
        str(here.parent.parent / "lcm2-pg") if here.parent.name == "context_engine" else None,
        "~/lcm2-mnemo2-pg/plugins/lcm2-pg",
        "~/.hermes/plugins/lcm2-pg",
    ]
    seen: set[pathlib.Path] = set()
    for cand in cands:
        if not cand:
            continue
        d = pathlib.Path(os.path.expanduser(cand)).resolve()
        if d in seen:
            continue
        seen.add(d)
        eng = d / "engine.py"
        if eng.is_file() and (d / "pgconn.py").is_file():
            # plugin modules import each other by bare name (pgconn, ddl,
            # tools, schemas...); expose the plugin dir on sys.path the way
            # the general plugin loader does when it imports the package.
            sp = str(d)
            if sp not in sys.path:
                sys.path.insert(0, sp)
            spec = importlib.util.spec_from_file_location(
                "lcm2_pg_plugin_engine", eng)
            if spec is None or spec.loader is None:
                raise ImportError(f"cannot build module spec for {eng}")
            mod = importlib.util.module_from_spec(spec)
            sys.modules["lcm2_pg_plugin_engine"] = mod
            spec.loader.exec_module(mod)
            return mod.LCM2Engine
    raise FileNotFoundError(
        "lcm2-pg plugin engine.py not found (set LCM2_PLUGIN_DIR)")


_engine_base: type | None = None


def _base() -> type:
    global _engine_base
    if _engine_base is None:
        _engine_base = _load_plugin_engine_class()
    return _engine_base


class LCM2PgContextEngine(ContextEngine):
    """Host ContextEngine that delegates every call to a lazily built
    lcm2-pg plugin engine (single implementation; the wrapper exists only
    so the class itself satisfies the isinstance gate BEFORE the plugin
    dir is ever touched). Constructor takes no arguments: dsn + schema
    bind from the environment inside the plugin engine (schema =
    lcm2_<basename(HERMES_HOME)>, never pinned)."""

    def __init__(self):
        self._impl = _base()()

    @property
    def name(self) -> str:  # host reads engine.name
        return "lcm2-pg"

    @name.setter
    def name(self, value: str) -> None:  # host may assign; accept & ignore
        pass

    def __getattr__(self, item):
        # attribute access on methods the BASE class does NOT define falls
        # through to the plugin engine. NOTE: names the base DOES define
        # (get_tool_schemas, handle_tool_call, get_status, on_turn_complete,
        # ...) resolve on the wrapper via normal MRO and never reach here —
        # each of those is explicitly forwarded below.
        if item.startswith("_"):
            raise AttributeError(item)
        return getattr(self._impl, item)

    # The three abstract ContextEngine methods need REAL defs (abstractness
    # is decided at class-creation time; __getattr__ cannot satisfy it), and
    # every OTHER base-defined method the host calls on the engine must be
    # forwarded explicitly, because the base's concrete defaults would
    # otherwise shadow __getattr__ (empty schemas, "Unknown tool", empty
    # status). Each forwarder is a one-line dispatch to the plugin engine.
    def update_from_response(self, usage) -> None:
        return self._impl.update_from_response(usage)

    def should_compress(self, prompt_tokens: int | None = None) -> bool:
        return self._impl.should_compress(prompt_tokens)

    def compress(self, messages, current_tokens=None, focus_topic=None,
                 force=False, memory_context=""):
        return self._impl.compress(messages, current_tokens,
                                   focus_topic=focus_topic, force=force,
                                   memory_context=memory_context)

    def get_tool_schemas(self) -> list:
        return self._impl.get_tool_schemas()

    def handle_tool_call(self, name, args, **kwargs) -> str:
        return self._impl.handle_tool_call(name, args, **kwargs)

    def get_status(self) -> dict:
        return self._impl.get_status()

    def should_compress_info(self, prompt_tokens=None):
        return self._impl.should_compress_info(prompt_tokens)

    def should_compress_preflight(self, messages) -> bool:
        return self._impl.should_compress_preflight(messages)

    def has_content_to_compress(self, messages) -> bool:
        return self._impl.has_content_to_compress(messages)

    def prune_tool_results_only(self, messages, current_tokens=None):
        return self._impl.prune_tool_results_only(messages, current_tokens)

    def on_turn_complete(self, messages, usage=None, **kwargs) -> None:
        return self._impl.on_turn_complete(messages, usage, **kwargs)

    def on_session_start(self, session_id, **kwargs) -> None:
        return self._impl.on_session_start(session_id, **kwargs)

    def on_session_end(self, session_id, messages) -> None:
        return self._impl.on_session_end(session_id, messages)

    def on_session_reset(self) -> None:
        return self._impl.on_session_reset()

    def update_model(self, *args, **kwargs) -> None:
        return self._impl.update_model(*args, **kwargs)

    def clone_for_agent(self, *_args, **_kwargs):
        """Fresh engine over the same durable backend (PG DSN + schema),
        never a deepcopy of locks/pools. Base clone_for_agent() takes no
        args; tolerate whatever the host passes."""
        impl = self._impl
        return LCM2PgContextEngine.from_impl(impl.dsn, impl.schema)

    @classmethod
    def from_impl(cls, dsn: str, schema: str):
        obj = cls.__new__(cls)
        obj._impl = _base()(dsn=dsn, schema=schema)
        return obj


def register(ctx):
    """Category-capture entry (context engines): the bridge engine."""
    ctx.register_context_engine(LCM2PgContextEngine())
