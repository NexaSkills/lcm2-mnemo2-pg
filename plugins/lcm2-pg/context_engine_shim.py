"""lcm2-pg context-engine bridge (loaded by the context-engine capture path).

The context-engine loader's capture calls ``register(ctx)`` on THIS module
when ``context.engine: lcm2-pg`` resolves to this directory — the loader
prefers a sibling file over the package ``__init__`` so the bridge never
re-runs the general plugin registration. It instantiates
``LCM2PgContextEngine`` — a real ``ContextEngine`` subclass (isinstance
gate satisfied) that delegates every protocol method to the plugin's own
LCM2Engine, imported file-anchored from this directory. The plugin
modules stay host-import-free (R3); this file is the single bridge.
"""
from __future__ import annotations

import importlib.util
import os
import pathlib
import sys

from agent.context_engine import ContextEngine


def _plugin_module(name: str):
    """Import <this dir>/<name>.py by FILE under a namespaced module name
    (bare `engine`/`tools`/`schemas` on sys.modules belong to whoever put
    them there — the host repo root does too — never resolve through them)."""
    here = pathlib.Path(__file__).resolve().parent
    full = f"{__name__}_impl_{name}"
    if full in sys.modules:
        return sys.modules[full]
    sp = str(here)
    if sp not in sys.path:  # the plugin modules import each other bare
        sys.path.insert(0, sp)
    spec = importlib.util.spec_from_file_location(full, here / f"{name}.py")
    if spec is None or spec.loader is None:
        raise ImportError(f"lcm2-pg bridge: cannot locate {name}.py in {here}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[full] = mod
    spec.loader.exec_module(mod)
    return mod


class LCM2PgContextEngine(ContextEngine):
    """Host ContextEngine wrapping the plugin engine (single
    implementation). Constructor takes no args: dsn + schema bind from the
    environment inside LCM2Engine (schema = lcm2_<basename(HERMES_HOME)>,
    never pinned)."""

    def __init__(self, dsn=None, schema=None):
        eng = _plugin_module("engine")
        self._impl = eng.LCM2Engine(dsn=dsn, schema=schema)

    @property
    def name(self) -> str:
        return "lcm2-pg"

    @name.setter
    def name(self, value):  # host may assign; accept & ignore
        pass

    def __getattr__(self, item):
        # Anything the base does NOT define (get_tool_schemas,
        # handle_tool_call, get_status, clone_for_agent, update_model, ...)
        # resolves on the plugin engine. Names the base DOES define resolve
        # on the wrapper via MRO and are forwarded explicitly below —
        # __getattr__ never sees them.
        if item.startswith("_"):
            raise AttributeError(item)
        return getattr(self.__dict__["_impl"], item)

    # -- abstract protocol (real defs required; abstractness is decided at
    # class-creation, __getattr__ cannot satisfy it) -----------------------
    def update_from_response(self, usage) -> None:
        self._impl.update_from_response(usage)

    def should_compress(self, prompt_tokens=None) -> bool:
        return self._impl.should_compress(prompt_tokens)

    def compress(self, messages, current_tokens=None, focus_topic=None,
                 force=False, memory_context=""):
        return self._impl.compress(messages, current_tokens,
                                   focus_topic=focus_topic, force=force,
                                   memory_context=memory_context)

    # -- base-CONCRETE methods the host calls (forwarding beats the base
    # defaults, which would answer empty schemas / Unknown tool) -----------
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

    def clone_for_agent(self):
        """Fresh engine over the same durable backend (DSN + schema), never
        a deepcopy of locks/pools. Host calls this with no args; tolerate
        extras."""
        return LCM2PgContextEngine(dsn=self._impl.dsn, schema=self._impl.schema)


def register(ctx):
    """Context-engine capture entry: hand the host the bridge engine."""
    ctx.register_context_engine(LCM2PgContextEngine())
