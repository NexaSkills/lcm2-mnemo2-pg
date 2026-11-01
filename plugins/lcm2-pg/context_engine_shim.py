"""lcm2-pg context-engine bridge (package attribute; loaded file-anchored).

context_engine_shim.py — NOT named engine.py on purpose: the host checkout
owns a top-level `engine` package, and under the context-engine capture
loader `sys.modules['engine']` may already hold the HOST's package. A file
named engine.py here would then be shadowed by that package for every
bare `import engine` in this directory (silent foreign LCM2Engine-less
import). This module holds ONLY the host-facing bridge:

  LCM2PgContextEngine — a real agent.context_engine.ContextEngine subclass
  (isinstance gate) that delegates every protocol method VERBATIM to the
  plugin's own LCM2Engine (single implementation; R3 isolation kept: the
  plugin modules never import host code, this bridge is the sole bridge).

The plugin engine class is resolved file-anchored from this directory via
_selfimport.anchor("engine"), then wrapped — never subclassed at import
time (the class body would freeze a wrong base). The wrapper delegates;
delegation survives any module-name confusion, subclassing does not.
"""
from __future__ import annotations

import sys

from agent.context_engine import ContextEngine

_HERE = __file__
import os as _os
_HERE = _os.path.dirname(_os.path.abspath(__file__))

_impl_cache: list = []


def _impl_class() -> type:
    """The plugin's LCM2Engine, loaded by FILE from THIS directory."""
    if _impl_cache:
        return _impl_cache[0]
    import importlib.util
    # Names on sys.path can lie (host `engine` package); file location cannot.
    path = _os.path.join(_HERE, "engine.py")
    spec = importlib.util.spec_from_file_location("lcm2_pg_engine", path)
    if spec is None or spec.loader is None:  # pragma: no cover
        raise ImportError(f"lcm2-pg: cannot load {path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("lcm2_pg_engine", mod)
    spec.loader.exec_module(mod)
    _impl_cache.append(mod.LCM2Engine)
    return mod.LCM2Engine


class LCM2PgContextEngine(ContextEngine):
    """Host ContextEngine; every call delegates to the plugin engine."""

    def __init__(self, dsn=None, schema=None):
        self._impl = _impl_class()(dsn=dsn, schema=schema)

    @property
    def name(self) -> str:
        return "lcm2-pg"

    @name.setter
    def name(self, value):  # host may assign; accept & ignore
        pass

    def __getattr__(self, item):
        if item.startswith("_"):
            raise AttributeError(item)
        return getattr(self._impl, item)

    # Base-class methods need explicit forwards: the base's concrete
    # defaults would otherwise shadow __getattr__ (empty schemas,
    # "Unknown tool", empty status). One-line dispatch each.
    def update_from_response(self, usage) -> None:
        return self._impl.update_from_response(usage)

    def should_compress(self, prompt_tokens=None) -> bool:
        return self._impl.should_compress(prompt_tokens)

    def compress(self, messages, current_tokens=None, focus_topic=None,
                 force=False, memory_context=""):
        return self._impl.compress(messages, current_tokens,
                                   focus_topic=focus_topic, force=force,
                                   memory_context=memory_context)

    def get_tool_schemas(self):
        return self._impl.get_tool_schemas()

    def handle_tool_call(self, name, args, **kwargs):
        return self._impl.handle_tool_call(name, args, **kwargs)

    def get_status(self):
        return self._impl.get_status()

    def should_compress_info(self, prompt_tokens=None):
        return self._impl.should_compress_info(prompt_tokens)

    def on_turn_complete(self, messages, usage=None, **kwargs):
        return self._impl.on_turn_complete(messages, usage, **kwargs)

    def on_session_start(self, session_id, **kwargs):
        return self._impl.on_session_start(session_id, **kwargs)

    def on_session_end(self, session_id, messages):
        return self._impl.on_session_end(session_id, messages)

    def on_session_reset(self):
        return self._impl.on_session_reset()

    def update_model(self, *args, **kwargs):
        return self._impl.update_model(*args, **kwargs)

    def should_compress_preflight(self, messages):
        return self._impl.should_compress_preflight(messages)

    def has_content_to_compress(self, messages):
        return self._impl.has_content_to_compress(messages)

    def prune_tool_results_only(self, messages, current_tokens=None):
        return self._impl.prune_tool_results_only(messages, current_tokens)

    def clone_for_agent(self, *args, **kwargs):
        return LCM2PgContextEngine(dsn=self._impl.dsn, schema=self._impl.schema)


def register(ctx):
    """Category-capture entry (context engine slot): the bridge engine."""
    ctx.register_context_engine(LCM2PgContextEngine())
