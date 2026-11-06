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

import os
import stat as _stat
import sys
import time
import types
from pathlib import Path

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
    A module ALREADY loaded under a legacy key gets the marker attribute
    (auditable neutralize) unless it is our own published provider
    package; keys not in sys.modules are claimed only when a spec exists
    (never clobber unrelated preloaded keys). Both stubs keep __file__ at
    the REAL origin so the loader's _hook_source_of() bookkeeping stays
    source-distinct from ours. MNEMO2_FAKE_LEGACY (contract test
    fixture): a fake legacy package pre-placed in sys.modules gets the
    marker too.
    """
    for legacy in ("mnemosyne_hermes", "mnemosyne"):
        cur = sys.modules.get(legacy)
        if cur is not None:
            if getattr(cur, "_mnemo2_shadow", False):
                continue  # already ours
            if legacy == "mnemosyne" and getattr(
                    cur, "__name__", "") == _PROVIDER_MOD:
                continue  # never fight our own provider package
            if (legacy == "mnemosyne_hermes"
                    and os.environ.get("MNEMO2_FAKE_LEGACY")):
                # contract-test fixture: a fake legacy pip package
                try:
                    cur._mnemo2_shadow = True
                except Exception:
                    pass
                continue
            # D-2 QUARANTINE (was: leave it — which let the legacy
            # sqlite-era provider keep answering the agent while our
            # zero-fallback :54331 path never ran): a LIVE legacy
            # binding is moved aside under a namespaced key instead of
            # staying in the bare slot. Audited by FILE CONTENT first
            # (a real provider); OUR published package and existing
            # shadows were skipped above; the contract-test fixture
            # (MNEMO2_FAKE_LEGACY) is exempted above too. Code already
            # imported FROM it by a racer keeps running from its own
            # references — we only vacate the slot, never unload.
            try:
                _f = getattr(cur, "__file__", "") or ""
                _src = ""
                if isinstance(_f, str) and os.path.isfile(_f):
                    with open(_f, encoding="utf-8", errors="replace") as fh:
                        _src = fh.read(200000)
            except OSError:
                _src = ""
            if ("MemoryProvider" in _src
                    or "register_memory_provider" in _src):
                sys.modules[f"_mnemo2_quarantine.{legacy}"] = cur
                try:
                    cur._mnemo2_quarantined_by = "mnemo-pg2"
                except Exception:
                    pass
                del sys.modules[legacy]
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

    The general-plugin boot may run this module WITHOUT its package
    parent in sys.modules; the package-qualified sibling import then
    falls back to loading OUR sibling accessor by file (same module
    objects register() published, thanks to _sibling's sys.modules-first
    path).
    """
    cur = sys.modules.get(_PROVIDER_MOD)
    if cur is not None and getattr(cur, "_mnemo2_provider_pkg", False):
        return cur

    import os

    try:
        from . import _sibling as _sibling_fn  # package-qualified, no bare key
    except Exception:  # no package parent in this boot
        import importlib.util as _ilu

        _spec = _ilu.spec_from_file_location(
            "_mnemo2_impl_memory_provider_sibling",
            os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "_sibling.py"))
        if _spec is None or _spec.loader is None:  # pragma: no cover
            raise ImportError("mnemo-pg2: sibling accessor missing")
        _m = _ilu.module_from_spec(_spec)
        _m.__package__ = ""  # standalone: our own file, no package route
        sys.modules[_spec.name] = _m
        try:
            _spec.loader.exec_module(_m)
        finally:
            sys.modules.pop(_spec.name, None)
        _sibling_fn = _m.sibling

    pe_mod = _sibling_fn("plugin_entry")
    cap_mod = _sibling_fn("capture")
    emb_mod = _sibling_fn("embed")
    _warm_done = {"v": False}
    _sleep_gate = {"t": 0.0}

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
            # S1a: remember the host-declared context (platform/
            # side_agent/parent_session_id are in the host's provider
            # init kwargs; vendor reads agent_context which our host
            # does not pass — we derive equivalently).
            self._platform = str(kwargs.get("platform") or "")
            self._side_agent = kwargs.get("side_agent")
            self._parent_sid = str(kwargs.get("parent_session_id") or "")
            # P4-B hardening: cold-load the embedding model OUT of the
            # turn path (daemon warm thread at session start). Without
            # it the FIRST capture in a short-lived process can be cut
            # off by CLI memory-shutdown before any write lands (the
            # model load cost sat inside the host's killed sync worker).
            if not _warm_done["v"]:
                _warm_done["v"] = True
                try:
                    import threading as _th

                    def _warm():
                        try:
                            emb_mod.embed_text("warm")
                        except Exception:
                            pass
                    _th.Thread(target=_warm, daemon=True).start()
                except Exception:
                    pass

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

                def _lines(tool, args, cap):
                    data = _json.loads(pe_mod._dispatch(tool, args))
                    mems = (data.get("memories") or data.get("results")
                            or data.get("data") or [])
                    got = []
                    for m in mems if isinstance(mems, list) else []:
                        body = (m.get("content") or m.get("body") or "") \
                            if isinstance(m, dict) else str(m)
                        body = str(body).strip()
                        if body:
                            got.append(body[:400])
                    return got[:cap]

                lines = _lines("mnemosyne_recall",
                               {"query": query, "limit": 5}, 8)
                seen = set(lines)
                # Shared (cross-agent) bank injection: SAME recall lane,
                # bank=surface. Never let a shared miss break private
                # injection — separate guard, small slice, deduped.
                shared = []
                try:
                    shared = [s for s in _lines(
                        "mnemosyne_recall",
                        {"query": query, "limit": 3, "bank": "surface"}, 3)
                        if s not in seen]
                except Exception:
                    shared = []
                if not lines and not shared:
                    return ""
                block = ("Relevant memories (mnemo-pg2):\n"
                         + "\n".join(lines)) if lines else ""
                if shared:
                    tail = ("Shared memories (mnemo-pg2, all agents):\n"
                            + "\n".join(f"- {s}" for s in shared))
                    block = (block + "\n" + tail) if block else tail
                return block
            except Exception:
                return ""

        def sync_turn(self, user_content=None, assistant_content=None,
                      **kwargs) -> None:
            # Host contract (agent/memory_provider.py MemoryProvider.
            # sync_turn): called as sync_turn(user, assistant, *,
            # session_id=..., messages=..., turn_author=...).
            # P4-B auto-capture (vendor parity), env-gated OFF by default:
            # MNEMO2_AUTO_CAPTURE=1 enables it per profile/host. Rows land
            # in the working tier as source='auto', low importance,
            # session scope — sanitized (machine envelopes stripped),
            # length-capped and ledger-idempotent (replayed turn = one
            # row). Never raises into the host turn loop.
            import os as _os
            if _os.environ.get("MNEMO2_AUTO_CAPTURE", "0") != "1":
                return None
            try:
                import json as _json
                sid = str(kwargs.get("session_id") or "") or self._session_id
                if not cap_mod.context_ok(
                        sid, platform=getattr(self, "_platform", ""),
                        side_agent=getattr(self, "_side_agent", None),
                        parent_session_id=getattr(self, "_parent_sid", "")):
                    return None  # S1a: non-primary context (vendor parity)
                self._maybe_auto_sleep()
                u = cap_mod.sanitize(user_content
                                     if isinstance(user_content, str)
                                     else (str(user_content or "")))
                a = cap_mod.sanitize(assistant_content
                                     if isinstance(assistant_content, str)
                                     else (str(assistant_content or "")))
                u = u if u and len(u) > 5 else None
                a = a if a and len(a) > 5 else None
                if u is None and a is None:
                    return None
                h = cap_mod.turn_hash(sid, u or "", a or "")
                for text, label, cap_len in ((u, "USER", 2000),
                                             (a, "ASSISTANT", 1200)):
                    if text is None:
                        continue
                    ticket = _json.loads(pe_mod._dispatch(
                        "mnemosyne__ledger_new_turn",
                        {"session_id": sid,
                         "turn_hash": f"{h}:{label[0].lower()}"}))
                    if not ticket.get("is_new"):
                        continue
                    pe_mod._dispatch(
                        "mnemosyne_remember",
                        {"content": f"[{label}] {text[:cap_len]}",
                         "importance": 0.3, "source": "auto",
                         "scope": "session", "session_id": sid})
            except Exception:
                return None
            return None

        def _maybe_auto_sleep(self) -> None:
            # S3 (vendor _maybe_auto_sleep parity, __init__.py:2880-2925):
            # on a turn that already passed context gates, cheap-count
            # unconsolidated rows older than half the working TTL; past
            # threshold (default 50, MNEMO2_AUTO_SLEEP_THRESHOLD) and
            # past the shared reflection budget, fire ONE daemon-thread
            # sleep. No timers, no daemons-with-loops: purely turn-
            # opportunistic, exactly like vendor.
            import os as _os
            if _os.environ.get("MNEMO2_AUTO_SLEEP", "1") != "1":
                return None
            import json as _json
            import threading as _th
            import time as _t
            try:
                if _t.time() - _sleep_gate["t"] < 60:
                    return None  # cheap in-process prefilter
                thr = int(_os.environ.get("MNEMO2_AUTO_SLEEP_THRESHOLD",
                                          "50"))
                d = _json.loads(pe_mod._dispatch("mnemosyne_sleep",
                                                 {"dry_run": True}))
                if int(d.get("eligible", 0)) < thr:
                    return None
                # cross-process reflection budget (vendor parity; the
                # meta-table claim is the real gate, DB is truth)
                b = _json.loads(pe_mod._dispatch(
                    "mnemosyne_reserve_budget",
                    {"key": "auto_sleep", "secs": 1800}))
                if not b.get("ok"):
                    return None
                _sleep_gate["t"] = _t.time()
                _th.Thread(target=lambda: pe_mod._dispatch(
                    "mnemosyne_sleep", {}), daemon=True).start()
            except Exception:
                return None
            return None

        def on_session_end(self, messages) -> None:
            # Session-boundary hook (fires at /new, /reset, /branch,
            # real exit — never per-turn). P4-C: with
            # MNEMO2_AUTO_CONSOLIDATE=1 this fires one ENGINE sleep()
            # hygiene pass (vendor beam.sleep analogue): age-gated
            # (>24h), consolidated_at-excluded, originals kept. Budget
            # guards: per-process 30-min cooldown, skipped entirely
            # when fewer than 10 rows are eligible (dry-run first),
            # 15s join cap (vendor budget), daemon thread so a session
            # end never blocks. Everything fail-soft.
            import os as _os
            if _os.environ.get("MNEMO2_AUTO_CONSOLIDATE", "0") != "1":
                return None
            import time as _t
            now = _t.time()
            if now - _sleep_gate["t"] < 60:
                return None  # cheap in-process prefilter
            try:
                import json as _json
                import threading as _th

                def _run():
                    try:
                        d = _json.loads(pe_mod._dispatch(
                            "mnemosyne_sleep", {"dry_run": True}))
                        if int(d.get("eligible", 0)) < 10:
                            return
                        b = _json.loads(pe_mod._dispatch(
                            "mnemosyne_reserve_budget",
                            {"key": "auto_sleep", "secs": 1800}))
                        if not b.get("ok"):
                            return
                        pe_mod._dispatch("mnemosyne_sleep", {})
                    except Exception:
                        pass
                th = _th.Thread(target=_run, daemon=True)
                th.start()
                th.join(15)
            except Exception:
                return None
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
    # Legacy published attribute (F-1 anchor); NOT what registration hands
    # out. The host's per-profile guard (memory_caller_scope.claim_instance)
    # is keyed by instance IDENTITY, and the wrapper is stateless (every
    # call routes through pe_mod._dispatch to the caller's own schema), so
    # registration yields a FRESH wrapper per profile init: one shared
    # Engine, one provider instance per agent — exactly what the host asks
    # for on multiplexed gateways (was: one instance shared by all, so
    # every profile after the first got memory_provider_instance_shared
    # and dropped to built-in memory).
    mod.provider = _MnemoPg2Provider()

    def register_memory_provider(ctx):
        # The one call the activation path owns (plugins/memory/
        # _ProviderCollector.register_memory_provider).
        _shadow_legacy()
        ctx.register_memory_provider(_MnemoPg2Provider())

    mod.register_memory_provider = register_memory_provider

    def register(ctx):
        # Directory-discovery activation path (plugins/memory/
        # _load_provider_from_dir calls mod.register(ctx), NOT
        # mod.register_memory_provider): yield a fresh wrapper bound to
        # the SAME Engine (F-1: one engine, per-profile instances).
        _shadow_legacy()
        try:
            ctx.register_memory_provider(_MnemoPg2Provider())
        except Exception:
            pass

    mod.register = register
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


def _provider_dir_shim() -> None:
    """Filesystem discovery shim (F-1 close item c, host-verified against
    plugins/memory/__init__.py on THIS build): load_memory_provider's
    FIRST route is directory discovery — _iter_provider_dirs/find_provider_dir
    scan bundled <repo>/plugins/memory/ then $HERMES_HOME/plugins/ with a
    literal child.is_dir() check that NEVER consults sys.modules. Pure
    in-memory publication (_extend_provider_prefixes below) is the
    second, opportunistic route only. So install() additionally authors
    a symlink $HERMES_HOME/plugins/mnemosyne -> the repo's neutral
    discovery dir (<repo>/plugins/mnemosyne/, authored by
    onboard_profile.sh step 5; memory_provider.py's own dir is NOT the
    symlink target, so the general PluginManager never scans this file's
    dir as a plugin). dirname matches, _is_memory_provider_dir passes,
    and that dir's register() hands the collector the SAME provider
    instance published here — one provider object, one Engine.
    In-repo + scripted = F-5-clean (no out-of-repo shim; the legacy
    machine-side symlink this replaces lived outside the repo).
    Target resolution: MNEMO2_BRIDGE_DIR env override (tests, unusual
    layouts), else <repo>/plugins/mnemosyne located from the plugin dir
    (repo layout = this package's grandparent's parent; standalone
    deploy = sibling "mnemosyne" dir next to this package).
    Idempotent and best-effort: wrong/foreign existing entries are left
    untouched, any failure is swallowed (the alias-table route stays).

    Reconciliation runs on EVERY install() (idempotent cheap lstat): an
    onboard or manual retarget of the entry (pointing at a legacy dir)
    is pulled back to our bridge, which keeps the activation path on
    OUR provider without ever clobbering a real directory install."""
    try:
        from hermes_constants import get_hermes_home

        home = get_hermes_home()
    except Exception:
        return
    if not home:
        return
    try:
        here = Path(__file__).resolve().parent
        bridge = os.environ.get("MNEMO2_BRIDGE_DIR", "").strip()
        if bridge:
            target_dir = Path(bridge).resolve()
        else:
            cand = here.parent.parent / "plugins" / "mnemosyne"
            if not (cand / "__init__.py").is_file():
                cand = here.parent / "mnemosyne"  # standalone deploy
            target_dir = cand
        if not (target_dir / "__init__.py").is_file():
            return  # discovery dir not shipped in this layout
        target = Path(home) / "plugins" / PROVIDER_NAME
        target.parent.mkdir(parents=True, exist_ok=True)
        lex = os.lstat(target)
        if not _stat.S_ISLNK(lex.st_mode):
            # D-2 self-heal: a REAL DIR here is an old-install legacy
            # entry (the machine-side mnemo-pg deploy left exactly
            # this) and directory discovery resolves it BEFORE any
            # in-memory route — legacy sqlite-era code answers the
            # slot. Retire it to a timestamped backup (same policy as
            # mnemo-pg's own mnemo_update.sh G3 quarantine) and
            # author the bridge symlink. A real dir we cannot retire
            # stays untouched (a genuine install; alias route survives).
            try:
                bak = Path(home) / "backups" / (
                    "mnemosyne-dir-quarantined-%s" % time.strftime(
                        "%Y%m%d-%H%M%S"))
                bak.parent.mkdir(parents=True, exist_ok=True)
                os.rename(str(target), str(bak))
            except OSError:
                return
        elif os.path.realpath(target) == str(target_dir):
            return  # already ours
        else:
            os.remove(str(target))
        os.symlink(str(target_dir), str(target))
    except FileNotFoundError:
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            os.symlink(str(target_dir), str(target))
        except Exception:
            pass
    except Exception:
        pass


def _latebind():
    try:
        from . import latebind as _lb  # package-qualified, no bare key
        return _lb
    except Exception:
        import importlib.util as _ilu
        spec = _ilu.spec_from_file_location(
            "_mnemo2_impl_latebind_file",
            os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "latebind.py"))
        if spec is None or spec.loader is None:  # pragma: no cover
            return None
        m = _ilu.module_from_spec(spec)
        sys.modules[spec.name] = m
        try:
            spec.loader.exec_module(m)
        finally:
            # keep it under a namespaced OURS key (the module attribute
            # on it is read later; register()'s purge may drop the key,
            # the object survives) — popping blind left _latebind
            # returning a never-executed stub when the key pre-existed.
            sys.modules.setdefault("_mnemo2_impl_latebind_pinned", m)
            if sys.modules.get(spec.name) is m:
                sys.modules.pop(spec.name, None)
        if not hasattr(m, "pin_engine"):  # dead stub (shouldn't happen)
            return None
        return m


def install() -> None:
    """Idempotent entry: shadow the legacy surface + publish the package
    (in-memory + filesystem discovery shim) + claim the late bare-name
    imports our provider-lifetime code makes (latebind)."""
    _shadow_legacy()
    _make_provider_module()
    _extend_provider_prefixes()
    _provider_dir_shim()
    lb = _latebind()
    if lb is not None:
        # THE late-bind immunity (boot-order independent, provider
        # lifetime): (1) anchor OUR late-imported files + front-load
        # OUR sibling finder, so bare imports from our files land on
        # our files even outside register()'s window; (2) pin the
        # module-globals (setattr on OUR modules only); (3) heal-wrap
        # the public entries so every later host call re-asserts the
        # pins on ITS OWN module copy (a rival plugin's loader that
        # anchored our file under a shared key outside our windows
        # cannot strand a foreign module-global).
        lb.install_serve_only()
        lb.pin_engine()
    _hw = None
    try:
        from . import healwrap as _hw  # package-qualified, no bare key
    except Exception:  # no package parent (by-file lane)
        import importlib.util as _ilu
        _sp = _ilu.spec_from_file_location(
            "_mnemo2_impl_healwrap_file",
            os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "healwrap.py"))
        if _sp is not None and _sp.loader is not None:
            _m = _ilu.module_from_spec(_sp)
            sys.modules["_mnemo2_impl_healwrap_file"] = _m
            try:
                _sp.loader.exec_module(_m)
                _hw = _m
            except Exception:
                _hw = None
    if _hw is not None:
        try:
            _hw.heal()
        except Exception:
            pass
