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

import importlib.util
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


def _engage():
    """Activate the file finder for the bridge's import chain.

    The host may capture the engine by its CLASS (subclass scan) without
    ever calling register(), and class-body attributes resolve at module
    import time — before any register(). Engaging from THIS frame's
    module globals works for both routes (register() call or dir(mod)
    scan). Disarms at exit, so the host's later imports of the same bare
    names (its own `tools`/`engine` packages) take their normal routes."""
    _ACTIVE[0] += 1

    def _disarm(_f=sys._getframe()):
        _ACTIVE[0] -= 1

    _disarm.__name__ = "_lcm2_disarm"
    return _disarm


def register(ctx):
    """Category-capture entry (context engine slot): the bridge engine."""
    _disarm = _engage()
    try:
        engine = LCM2PgContextEngine()
    finally:
        _disarm()
    ctx.register_context_engine(engine)


_disarm_module = _engage()
try:
    _warm = _impl_class()  # resolves engine -> pgconn -> ddl by file
finally:
    _disarm_module()


# The context-engine capture loader may instantiate the engine class it
# finds in the module namespace WITHOUT calling register(), and it imports
# our modules as SUBMODULES of a synthetic namespace
# (_hermes_user_context_engine.lcm2-pg.*). Bare sibling imports (pgconn,
# tools, schemas, ...) then have no resolution path at all, and mutating
# sys.path would let the HOST's root modules (tools package, engine
# package) resolve wrongly from then on (observed: later host import
# `tools.transcription_common` exploded because `tools` resolved to our
# FILE). So: resolve siblings by FILE into namespaced module names only
# (lcm2_pg_fs_<name>), injected as each module's __loader__-independent
# sys.modules entry. No bare-name registrations, no sys.path edits.
_FSTYPE = ("pgconn", "ddl", "rollup_periods", "engine", "tools", "schemas",
           "hooks", "command")


def _fsib(name: str):
    """Our sibling <name>.py by FILE, in a namespaced sys.modules slot.

    Idempotent. tools.py / hooks.py import pgconn etc. BARE, so after
    registering the namespaced copies we also expose them under their
    bare names ONLY while no foreign holder exists; a foreign holder is
    never evicted from this path (the engine's own _sib/_own_* file
    resolution is the primary route; this is the belt for the module-
    namespace-import route the loader may take)."""
    full = f"lcm2_pg_fs_{name}"
    cur = sys.modules.get(full)
    if cur is not None and getattr(cur, "__file__", None):
        return cur
    path = _os.path.join(_HERE, name + ".py")
    if not _os.path.isfile(path):
        raise ImportError(f"lcm2-pg bridge: no {name}.py in {_HERE}")
    spec = importlib.util.spec_from_file_location(full, path)
    if spec is None or spec.loader is None:  # pragma: no cover
        raise ImportError(f"lcm2-pg bridge: bad spec for {path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[full] = mod
    spec.loader.exec_module(mod)
    return mod


# Chain the siblings in import order so their OWN bare imports find the
# previously registered namespaced modules through a tiny meta-path hook
# scoped to OUR module names (never touches foreign names).
_ACTIVE = [0]  # >0 while OUR frames are importing


def _engaged():
    f = sys._getframe(1)
    while f is not None:
        fn = getattr(f, "f_globals", {}).get("__file__", "")
        if isinstance(fn, str) and _os.path.dirname(
                _os.path.realpath(fn)) == _HERE:
            return True
        f = f.f_back
    return False


class _Lcm2FileFinder:
    """Meta-path finder that resolves the plugin's OWN module names from
    this directory by file — but ONLY while a frame from our directory is
    on the import stack. Foreign imports (the host's `tools
    .transcription_common`, `engine.tool_*`) take their normal routes:
    a global file redirect under our names would hijack them."""

    _names = set(_FSTYPE) & set()  # replaced below; see find_spec

    def find_spec(self, fullname, path=None, target=None):
        if fullname not in _FSTYPE or not (_ACTIVE[0] or _engaged()):
            return None
        p = _os.path.join(_HERE, fullname + ".py")
        if not _os.path.isfile(p):
            return None
        return importlib.util.spec_from_file_location(fullname, p)


if not any(type(f).__name__ == "_Lcm2FileFinder" and getattr(f, "_names_marker", None) == _HERE
           for f in sys.meta_path):
    _finder = _Lcm2FileFinder()
    _finder._names_marker = _HERE
    sys.meta_path.insert(0, _finder)
