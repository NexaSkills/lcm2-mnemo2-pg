"""Host memory-provider contract for mnemo-pg2 (F-1 fix).

WHY THIS FILE EXISTS (lcm2-mnemo2-redesign F-1):
The host activates the memory backend through ``memory.provider`` via
plugins/memory/__init__.py::load_memory_provider(name), which is a
DISCOVERY PATH SEPARATE from the general PluginManager: it looks for a
directory NAMED the provider ("mnemosyne") under bundled
``<repo>/plugins/memory/``, then ``$HERMES_HOME/plugins/mnemosyne/``,
then pip entry points. mnemo-pg2 was only registered with the general
PluginManager (which registers tools/hooks fine), so nothing ever
answered the memory-provider slot: every boot warned "Memory provider
'mnemosyne' is not installed" and R4 (real-session mnemosyne tools)
failed. The old machine-side answer lived OUTSIDE the repo (the legacy
mnemo-pg loader shadowed the slot via a symlink + _neutralize_legacy.py)
— an F-5 clean-checkout violation. This file + the two-line wiring below
put the whole mechanism IN the repo, versioned.

HOW IT RESOLVES (clean checkout, zero profile-side steps):
mnemo-pg2's own register() (the general PluginManager path, already run
by onboard_profile.sh's `hermes plugins enable`) publishes an in-memory
package ``_hermes_memory_providers.mnemosyne`` into sys.modules BEFORE
returning. plugins/memory/__init__.py::_iter_provider_dirs scans
``<repo>/plugins/memory/`` with a literal ``child.is_dir()`` check, which
follows a filesystem symlink but NEVER an in-memory module — so the
general PluginManager's own meta-path finder (_UserPluginFinder, whose
prefix tuple we append below) answers the provider lookup straight out
of this package. No filesystem symlink is created by anything in the
repo; both plugin lanes (provider slot + tools/hooks) bind to ONE
source of truth: this checkout.

Legacy shadowing (neutralize): the host boot also discovers the LEGACY
pip package ``mnemosyne_hermes`` (site-packages / entry points), whose
provider would answer agent calls with sqlite-era code paths (the old
out-of-repo _neutralize_legacy.py reason). _install_shadows() runs at
EVERY entry point (register / load / instantiate), idempotently, and
claims both provider keys (see _shadow_legacy).
"""
from __future__ import annotations

import sys
import types

# The provider NAME the host config selects: memory.provider: mnemosyne
PROVIDER_NAME = "mnemosyne"
_PROVIDER_PKG = "_hermes_memory_providers"
_PROVIDER_MOD = f"{_PROVIDER_PKG}.{PROVIDER_NAME}"


# ------------------------------------------------------------- shadows

def _stub(name: str, doc: str) -> types.ModuleType:
    mod = types.ModuleType(name)
    mod.__doc__ = doc

    def _dead(*_a, **_k):
        raise RuntimeError(
            f"{name} is disabled by mnemo-pg2; the mnemosyne_* tools are "
            "served by the mnemo-pg2 provider (PG-native, dedicated "
            "instance)")

    for _n in ("register", "register_memory_provider",
               "get_memory_provider", "initialize", "init"):
        setattr(mod, _n, _dead)
    return mod


def _shadow_legacy() -> None:
    """Claim the legacy provider keys so nothing else can serve 'mnemosyne'.

    'mnemosyne_hermes'  — legacy pip package (mirrors the out-of-repo
       mnemo-pg/_neutralize_legacy.py mechanism, brought in-repo per F-5).
    'mnemosyne'         — legacy top-level package that imports CLEAN on
       this host, so the general PluginManager scans it as an ordinary
       plugin with provider-style hooks; left unclaimed it registers
       alongside mnemo-pg2 (duplicate hooks/tools, and its entry point
       would answer the provider slot). Claiming it costs nothing: our
       provider module itself hides under _hermes_memory_providers.mnemosyne,
       never the bare key.
    Both stubs keep __file__ at the REAL origin so the loader's
    _hook_source_of() bookkeeping stays source-distinct from ours.
    """
    for legacy in ("mnemosyne_hermes", "mnemosyne"):
        cur = sys.modules.get(legacy)
        if cur is not None:
            if getattr(cur, "_mnemo2_shadow", False):
                continue  # already ours
            if legacy == "mnemosyne" and getattr(
                    cur, "__name__", "") == _PROVIDER_MOD:
                continue  # never fight our own provider package
            # A live import may already hold code we cannot audit; leave it
            # (the memory-provider slot still resolves to OUR module by
            # name) but never overwrite a loaded module mid-import.
            continue
        try:
            import importlib.util

            spec = importlib.util.find_spec(legacy)
        except Exception:
            spec = None
        if spec is None or spec.origin is None:
            continue  # not installed here: nothing to shadow
        stub = _stub(legacy, f"Inert shadow installed by mnemo-pg2 for {legacy}.")
        stub.__file__ = spec.origin
        stub.__path__ = []  # empty package: subimports fail
        stub._mnemo2_shadow = True
        sys.modules[legacy] = stub


# ----------------------------------------------------- provider module

def _make_provider_module():
    """Build (and register in sys.modules) the package that answers
    load_memory_provider('mnemosyne').

    Deliberately NOT a subclass of the host MemoryProvider ABC: the ABC
    import would drag host modules in at module-exec time under the
    general-plugin boot. The loader's contract needs only
    ``register_memory_provider(ctx)`` on the module (or a top-level
    MemoryProvider subclass); our class implements the whole surface the
    MemoryManager drives (is_available/initialize/get_tool_schemas/
    handle_tool_call/shutdown, per agent/memory_provider.py).
    """
    cur = sys.modules.get(_PROVIDER_MOD)
    if cur is not None and getattr(cur, "_mnemo2_provider_pkg", False):
        return cur

    import os

    from . import _sibling as _sibling_fn  # package-qualified, no bare key

    pe_mod = _sibling_fn("plugin_entry")

    class _MnemoPg2Provider:
        """MemoryProvider for mnemo-pg2: the SAME Engine every mnemosyne_*
        tool dispatches through (plugin_entry._dispatch), so provider
        lifecycle calls and tool calls hit identical PG routes."""

        name = PROVIDER_NAME

        def __init__(self):
            self._session_id = ""
            self._initialized = False

        # -- core lifecycle ------------------------------------------------
        def is_available(self) -> bool:
            # Availability = config only, no network (host contract): the
            # DSN env is what onboard_profile.sh writes into the profile
            # .env. Connection problems surface at use time, honestly.
            try:
                pgconn = _sibling_fn("pgconn")
                return bool(pgconn.default_dsn())
            except Exception:
                return bool(os.environ.get("MNEMO2_PG_DSN", "").strip())

        def unavailable_reason(self) -> str:
            if not self.is_available():
                return ("MNEMO2_PG_DSN is not set for this profile "
                        "(onboard_profile.sh writes it into the profile .env)")
            return ""

        def initialize(self, session_id: str, **kwargs) -> None:
            self._session_id = str(session_id or "")
            self._initialized = True

        def system_prompt_block(self) -> str:
            # Copy lives repo-side so it survives with no DB reachable;
            # kept SHORT: this block rides every request.
            return ("Persistent memory is active (mnemo-pg2, PostgreSQL-"
                    "native). Use the mnemosyne_* tools to store and "
                    "recall durable facts; recall before re-deriving.")

        def prefetch(self, query: str, *, session_id: str = "") -> str:
            # Recall injection: ONE recall call through our own dispatch
            # (same binding/env path as the tool surface). Returns plain
            # text lines; empty on any miss/error (never raises into the
            # host turn loop).
            if not (query or "").strip():
                return ""
            try:
                import json as _json

                out = pe_mod._dispatch(
                    "mnemosyne_recall",
                    {"query": query, "limit": 5})
                data = _json.loads(out)
                mems = (data.get("memories") or data.get("results")
                        or data.get("data") or [])
                lines = []
                for m in mems if isinstance(mems, list) else []:
                    body = (m.get("content") or m.get("body") or "") \
                        if isinstance(m, dict) else str(m)
                    body = str(body).strip()
                    if body:
                        lines.append(f"- {body[:400]}")
                if not lines:
                    return ""
                return ("Relevant memories (mnemo-pg2):\n"
                        + "\n".join(lines[:8]))
            except Exception:
                return ""

        def sync_turn(self, messages=None, **kwargs) -> None:
            # Write capture stays tool-mediated (mnemosyne_remember):
            # automatic per-turn retention would write UNFILTERED chat
            # into the agent's private schema (F-4 rows must be real
            # memories). on_session_end stays default no-op for the
            # same reason.
            return None

        def shutdown(self) -> None:
            self._initialized = False

        # -- tool surface (the 42 names, same schemas as PluginManager) ----
        def get_tool_schemas(self) -> list:
            try:
                names = pe_mod._wildcard_names()
            except Exception:
                names = []
            out = []
            for n in names:
                try:
                    out.append(pe_mod._schema_for(n))
                except Exception:
                    continue
            return out

        def handle_tool_call(self, tool_name: str, args: dict, **kwargs) -> str:
            # THE F-1 PROOF LINE: same _dispatch as the PluginManager path
            # -> same Engine -> same per-agent schema (F-4: caller's
            # private schema), zero legacy DBs touched.
            return pe_mod._dispatch(tool_name, dict(args or {}))

    mod = types.ModuleType(_PROVIDER_MOD)
    mod.__doc__ = ("mnemo-pg2 memory provider package published IN-MEMORY "
                   "by plugins/mnemo-pg2 (see memory_provider.py).")
    mod.__file__ = getattr(sys.modules.get(__name__), "__file__", __file__)
    mod.__path__ = []  # package-like for loader bookkeeping
    mod.MnemosyneMemoryProvider = _MnemoPg2Provider
    mod.provider = _MnemoPg2Provider()

    def register_memory_provider(ctx):
        # The one call the activation path owns (plugins/memory/
        # _ProviderCollector.register_memory_provider).
        _shadow_legacy()
        ctx.register_memory_provider(mod.provider)

    mod.register_memory_provider = register_memory_provider
    mod._mnemo2_provider_pkg = True

    pkg = sys.modules.get(_PROVIDER_PKG)
    if pkg is None:
        pkg = types.ModuleType(_PROVIDER_PKG)
        pkg.__path__ = []  # namespace-ish parent so children resolve
        sys.modules[_PROVIDER_PKG] = pkg
    setattr(pkg, PROVIDER_NAME, mod)
    sys.modules[_PROVIDER_MOD] = mod
    _extend_provider_prefixes()
    return mod


def _extend_provider_prefixes() -> None:
    """Let the general PluginManager's meta-path finder answer
    ``plugins.memory.mnemosyne`` (the module name plugins/memory uses for
    a BUNDLED provider) by delegating to OUR package.

    Without this, load_memory_provider would import a *second* copy of
    this module under plugins.memory.mnemosyne and build a second
    provider instance (harmless, but two objects on one contract). With
    it, sys.modules identity holds: one provider object everywhere.
    """
    for finder in list(sys.meta_path):
        prefixes = getattr(finder, "_prefixes", None)
        if prefixes is None:
            continue
        for alias in ("plugins.memory", "plugins\\memory"):
            target = f"{alias}.{PROVIDER_NAME}"
            if target not in prefixes:
                try:
                    prefixes[target] = _PROVIDER_MOD
                except Exception:
                    pass
    # Package-attribute belt for `import plugins.memory` then attribute
    # access (plugins.memory itself resolves the normal way).
    pm = sys.modules.get("plugins.memory")
    if pm is not None:
        try:
            setattr(pm, PROVIDER_NAME, sys.modules[_PROVIDER_MOD])
        except Exception:
            pass


def install() -> None:
    """Idempotent entry: shadow the legacy surface + publish the package."""
    _shadow_legacy()
    _make_provider_module()
    _extend_provider_prefixes()
