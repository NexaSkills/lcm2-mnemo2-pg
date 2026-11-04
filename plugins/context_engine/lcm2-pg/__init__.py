"""lcm2-pg context engine — host-native resolution dir (F1).

The host's context-engine discovery (plugins/context_engine/__init__.py:
find_engine_dir) probes CHILD DIRECTORIES of $HERMES_HOME/plugins/ whose
__init__.py mentions ContextEngine — a symlink to the general plugin
package is never treated as an engine, so this dir exists to be the
resolution home for ``context.engine: lcm2-pg`` (profile layout:
$HERMES_HOME/plugins/context_engine/lcm2-pg).

NO BEHAVIOR LIVES HERE (one-line justification for this file's existence:
the discovery heuristic needs a dir; the implementation must stay single
in plugins/lcm2-pg/). The host's module scan runs BEFORE register() and
picks any ContextEngine subclass it finds in the module namespace, so we
expose exactly one: the bridge class from the general package
(plugins/lcm2-pg/context_engine_shim.py — a real
agent.context_engine.ContextEngine subclass that delegates every
protocol method verbatim to the plugin's LCM2Engine).

The package import inside _package() is file-anchored by absolute path
(never sys.path), because the discovery scan imports every child dir's
modules and cannot be relied on to have anchored the package first.

C2RESID2S SCHEMA-ENSURE LEG (the silent NO-INSTANCE cure): every engine
construction (this register() included) ends in LCM2Engine.__init__ ->
Conn bootstrap, whose unqualified CREATE TABLEs run under
``search_path=<derived schema>``. If that schema does not EXIST, Postgres
answers InvalidSchemaName ("no schema has been selected to create in")
and the capture loader swallows it — NO-INSTANCE with every link and
import green (beez proof 2026-11-04: schema lcm2__hermes absent on
:54331 while lcm2_default/lcm2_buttercup existed; with the schema
authored, the SAME constructor path built all 37 tables, verified).
Per-profile schemas are created by scripts/onboard_profile.sh §2 and the
launch-scope one by its root leg; that leaves a race this leg closes:
first boot BEFORE any onboard run (a freshly named profile, a renamed
home) would die here forever. So ensure the target schema exists (one
idempotent CREATE SCHEMA IF NOT EXISTS over the default search_path)
before demanding the engine construct. Fail-soft by design: any DB
unreachable here leaves the pre-C2RESID2S behavior untouched (loud
FileNotFoundError-free, host falls back with its accurate warning).
"""
from __future__ import annotations

import importlib.util as _ilu
import os as _os
import pathlib as _pathlib
import re as _re
import sys as _sys

# Marker read by the loader's dir heuristic (_is_context_engine_dir):
# ContextEngine / register_context_engine must appear in this file.
_ENGINE_MARKER = "ContextEngine"

_SCHEMA_RE = _re.compile(r"^[a-z_][a-z0-9_]*$")


def _ensure_schema(pkg) -> None:
    """Idempotent CREATE SCHEMA IF NOT EXISTS for the schema the engine is
    about to bind (pgconn.default_dsn/agent_schema — the SAME derivation the
    engine constructor uses, so no second source of truth). NEVER raises:
    the whole point is that a failure here must leave the pre-existing
    behavior byte-identical (the engine's own bootstrap then fails exactly
    as loud — or as silently — as before this leg existed)."""
    try:
        import importlib.util as _u
        pg = _sys.modules.get("_lcm2_pg_ce_pgconn")
        if pg is None or not (callable(getattr(pg, "default_dsn", None))
                              and callable(getattr(pg, "agent_schema", None))):
            # resolve by file through the SAME layout _package() uses:
            # checkout sibling first (authoritative — the repo dir this
            # capture dir physically lives in), then env override and the
            # host plugin locations. The sibling-first order matters: a
            # stray LCM2_PLUGIN_DIR from a foreign surface must never
            # redirect the schema derivation of THIS package's engine.
            here = _pathlib.Path(__file__).resolve().parent.parent.parent / "lcm2-pg"
            cands = [here, _os.environ.get("LCM2_PLUGIN_DIR"),
                     "~/lcm2-mnemo2-pg/plugins/lcm2-pg",
                     "~/.hermes/plugins/lcm2-pg"]
            f = None
            for cand in cands:
                if not cand:
                    continue
                p = _pathlib.Path(_os.path.expanduser(str(cand))) / "pgconn.py"
                if p.is_file():
                    f = p
                    break
            if f is None:
                return
            spec = _u.spec_from_file_location("_lcm2_pg_ce_pgconn", f)
            if spec is None or spec.loader is None:
                return
            pg = _u.module_from_spec(spec)
            _sys.modules["_lcm2_pg_ce_pgconn"] = pg
            try:
                spec.loader.exec_module(pg)
            except BaseException:
                _sys.modules.pop("_lcm2_pg_ce_pgconn", None)
                return
        dsn = pg.default_dsn()
        schema = pg.agent_schema()
        if not _SCHEMA_RE.match(schema):
            return  # never interpolate an unvalidated identifier
        # Leg-boundary schema pin (C2RESID2S): pin the derivation NOW,
        # before psycopg is even touched, so the engine constructor that
        # follows — which re-derives the schema itself — provably lands
        # on the SAME schema this leg authors, whatever psycopg.connect
        # does in between. Set only when ABSENT: an existing pin is
        # somebody else's (an onboard leg, a test isolation guard) and
        # is never clobbered, only honored. The guard below is
        # load-bearing: pgconn itself may have died on a dead import
        # chain (ruamel/yaml absent on the gateway tools-exe) leaving a
        # PARTIAL, function-less module object — the getattr answers
        # None and the leg exits fail-soft, never wrongly pins.
        _pinned = ""
        if not _os.environ.get("LCM2_AGENT_SCHEMA"):
            _derived = getattr(pg, "agent_schema", lambda: None)()
            if _derived and _SCHEMA_RE.match(_derived):
                _os.environ["LCM2_AGENT_SCHEMA"] = _pinned = _derived
        import psycopg
        # psycopg_pool pre-check (C2RESID2S): pgconn's OWN depcheck
        # demands psycopg_pool at import. Connecting WITHOUT the pool
        # importable would leave a psycopg binding cached that the
        # package's depcheck would then wrongly accept (X-4 fail-close
        # contract; the purge in this finally predates the leg — the
        # pre-check just stops using psycopg BEFORE the purge decides).
        import importlib.util as _u0
        if _u0.find_spec("psycopg_pool") is None:
            return  # engine couldn't run anyway; leave everything pristine
        try:
            try:
                with psycopg.connect(dsn, autocommit=True) as cx:
                    cx.execute('CREATE SCHEMA IF NOT EXISTS "%s"' % schema)
            finally:
                if _pinned and _os.environ.get("LCM2_AGENT_SCHEMA") == _pinned:
                    del _os.environ["LCM2_AGENT_SCHEMA"]  # leave no residue
        finally:
            # NEVER leave psycopg cached as "importable" if the package's
            # own depcheck rejects the pool it demands (pgconn imports
            # psycopg_pool at module scope; a stale psycopg/psycopg_pool
            # key would skip that check — X-4 fail-close contract).
            _sys.modules.pop("psycopg", None)
            if _u0.find_spec("psycopg_pool") is None:
                _sys.modules.pop("psycopg_pool", None)
    except BaseException:
        pass  # fail-soft: pre-C2RESID2S behavior stands (see docstring)



def _package():
    """Import the general lcm2-pg plugin package by FILE (namespaced;
    publishes no bare names). Candidate order: env override, repo sibling
    (checkout layout), the profile/global plugin locations."""
    if "_lcm2_pg_ce_pkg" in _sys.modules:
        return _sys.modules["_lcm2_pg_ce_pkg"]
    here = _pathlib.Path(__file__).resolve().parent
    cands = [
        _os.environ.get("LCM2_PLUGIN_DIR"),
        str(here.parent.parent / "lcm2-pg"),   # checkout: plugins/context_engine/lcm2-pg -> sibling
        "~/lcm2-mnemo2-pg/plugins/lcm2-pg",
        "~/.hermes/plugins/lcm2-pg",
    ]
    for cand in cands:
        if not cand:
            continue
        init = _pathlib.Path(_os.path.expanduser(cand)) / "__init__.py"
        if init.is_file():
            spec = _ilu.spec_from_file_location(
                "_lcm2_pg_ce_pkg", init,
                submodule_search_locations=[str(init.parent)])
            if spec is None or spec.loader is None:  # pragma: no cover
                raise ImportError(f"lcm2-pg ce dir: bad spec for {init}")
            mod = _ilu.module_from_spec(spec)
            _sys.modules["_lcm2_pg_ce_pkg"] = mod
            spec.loader.exec_module(mod)
            return mod
    raise FileNotFoundError(
        "lcm2-pg context-engine dir: plugin package __init__.py not found "
        "(set LCM2_PLUGIN_DIR)")


try:
    LCM2PgContextEngine = _package().LCM2PgContextEngine
except Exception:  # package unreachable: stay inert, host falls back loudly
    LCM2PgContextEngine = None


def register(ctx):
    """Category-capture entry (context engines): the bridge engine.

    Import the bridge through the package (single file-anchored
    definition; the package anchors ITS bare siblings — engine, pgconn,
    tools, schemas — by file location before any of them import each
    other).

    X-1 engine-slot precedence: when the legacy lcm-pg plugin is also
    installed, the explicit rule in engine_slot.py decides the slot —
    on a LOSS this entry logs the ONE yield line and skips a doomed
    claim; on a WIN against an already-claimed slot it retires the rival
    lease and claims here. Single-stack: unchanged, one claim."""
    claim = LCM2PgContextEngine
    if claim is None:
        return
    pkg = _package()
    try:
        slot = pkg.engine_slot
    except Exception:
        slot = None

    def _slot_attempt():
        _ensure_schema(pkg)  # C2RESID2S: derived-schema race cure (fail-soft)
        ctx.register_context_engine(claim())

    import logging
    if slot is None:
        _slot_attempt()
    else:
        slot.enforce_slot(ctx, "lcm2-pg", _slot_attempt,
                          logging.getLogger("lcm2-pg"))
