"""Shared fixtures for lcm2-pg tests.

Everything runs against the REAL dedicated instance (socket
~/lcm2-mnemo2-pg/pg/run, port 54331, db lcm2db) over the SAME connection
path the plugin uses in production. No embedded DB anywhere (R3).
"""
import os
import sys
import pathlib

import pytest

REPO = pathlib.Path(__file__).resolve().parents[2]
PLUGIN_DIR = str(REPO / "plugins" / "lcm2-pg")
sys.path.insert(0, PLUGIN_DIR)

import pgconn  # noqa: E402  (plugin module)
import ddl as _plugin_ddl  # noqa: E402

# Neutralize ANY 'ddl' module shadowed into sys.modules by sibling pytest
# suites (mnemo-pg2's repo-root tests add their own plugins/mnemo-pg2 dir to
# sys.path): this suite's bootstrap must use lcm2-pg's OWN ddl module.
_sys_ddl = sys.modules.get("ddl")
if _sys_ddl is not _plugin_ddl:
    sys.modules["ddl"] = _plugin_ddl

SOCKET = os.environ.get(
    "LCM2_PG_HOST", os.path.expanduser("~/lcm2-mnemo2-pg/pg/run"))
DB = "lcm2db"
SIBLING_DB = "mnemo2db"  # N-3 (qa4): combined runs let the SIBLING suite's
# lcm2-flavoured fixtures bootstrap the lcm2 engine against mnemo2db, whose
# public holds vendor-era residue — pgconn's public-busy guard then kills
# every lcm2 schema fixture there. The sweep below therefore empties public
# in BOTH databases of this instance, whichever conftest gets there first.
BASE_DSN = f"dbname={DB} host={SOCKET} port=54331 user=mnemo201"

# Pin the plugin's default DSN + disable the summary model for determinism
# (tests that specifically test DSN resolution reset this).
os.environ["LCM2_PG_DSN"] = BASE_DSN
os.environ["LCM2_DISABLE_MODEL"] = "1"


@pytest.fixture(scope="session")
def dsn():
    pgconn.shutdown_pools()
    try:
        with pgconn.connect(BASE_DSN, "public") as cx:
            assert cx.query_scalar("select 1") == 1
    except Exception as e:
        pytest.fail(f"lcm2 instance unreachable at {SOCKET}:54331 db={DB}: {e}"
                    " (start scripts/ensure_instance.sh; never falls back, R3)")
    # Sweep throwaway schemas LEFT BY PREVIOUS runs before this one mints
    # its own. WHY: the `schema` fixture DROPs in teardown — a run killed
    # mid-suite (SIGKILL/timeout/Ctrl-C, incl. a concurrent lane's run being
    # stopped) skips teardown and leaves lcm2_test_<uuid> behind; the F3
    # residue tripwire then fails an UNRELATED later run (qa round showed
    # Sweep classes are throwaway by construction; profile
    # schemas (lcm2_lcm2agent/lcm2_mnemo2agent/shared) never match. qa4
    # classes: the SIBLING suite's throwaway families (mnemo2_test_*/
    # shared_test_*) also land in lcm2db when combined ordering lets its
    # fixtures bootstrap against this db, and *_qa4*/lcm2_r4test* are
    # gate-probe residue naming conventions.
    import re
    _family = re.compile(r"^(lcm2_test_.*|lcm2_probe.*|probe_.*|lcm2_dbg_.*|lcm2_qa.*|lcm2_r4test.*|mnemo2_qa.*|mnemo2_test_.*|mnemo2_probe.*|shared_test_.*|p3[ab]?)$")
    # qa3 N-3 (lcm2db half): legacy vendor-era TABLES in schema `public`
    # (mnemosyne-era leftovers, 40 of them on the shared instance) trip
    # pgconn's ddl public-busy guard in EVERY schema fixture below —
    # same class the mnemo2 side already sweeps. Throwaway by
    # construction (agent data lives in agent schemas, never public);
    # the drop is cascade'd and the sweep classes are identical to
    # scripts/sweep_public_residue.py's.
    # N-3 (qa4 root fix): BOTH databases of the instance are swept, not just
    # this suite's. In COMBINED ordering the sibling suite's lcm2-flavoured
    # fixtures bootstrap the lcm2 engine against the OTHER db, and public
    # residue there trips pgconn's ddl public-busy guard (pgconn.py
    # 'refusing ddl bootstrap while schema public holds relations') in every
    # lcm2 schema fixture. Worse, a public table AT REST re-appears live:
    # mnemo-pg2's shared-view bake bakes `CREATE VIEW <schema>.<name> AS
    # SELECT * FROM public.<name>` for every public table into each freshly
    # bootstrapped schema, so one db's residue repopulates the other's
    # public during a run. A sweep that only drops tables is therefore not
    # enough at rest: public views are dropped too (a stale view is exactly
    # what re-exposes base tables through the bake).
    for _db in (DB, SIBLING_DB):
        _dsn = f"dbname={_db} host={SOCKET} port=54331 user=mnemo201"
        with pgconn.connect(_dsn, "public") as cx:
            # (view/matview/foreign first, tables after: a view cannot drop
            # after its base table vanished — CASCADE covers the reverse)
            stale_rels = [
                (r["relname"], r["relkind"]) for r in cx.query_dicts(
                    "select c.relname, c.relkind from pg_class c "
                    "join pg_namespace n on n.oid = c.relnamespace "
                    "where n.nspname = 'public' "
                    "and c.relkind in ('v','m','f','r','p') "
                    "order by (c.relkind in ('r','p')), c.relname")]
            for rel, kind in stale_rels:
                stmt = "DROP VIEW" if kind in "vmf" else "DROP TABLE"
                cx.execute(f'{stmt} IF EXISTS public."{rel}" CASCADE')
            stale = [r["nspname"] for r in cx.query_dicts(
                "select nspname from pg_namespace")
                if _family.match(r["nspname"])]
            for n in stale:
                cx.execute(f'DROP SCHEMA IF EXISTS "{n}" CASCADE')
    return BASE_DSN


@pytest.fixture
def registered(dsn, schema):
    """Registered plugin surface (fresh FakeCtx + engine in a throwaway schema).
    Shared by test_r4_tools_surface and test_r4_full_surface."""
    import importlib
    import os
    os.environ["LCM2_DISABLE_MODEL"] = "1"  # deterministic extractive summaries
    import engine as E
    eng = E.LCM2Engine(dsn=dsn, schema=schema)
    entry = importlib.import_module("plugin_entry")

    class FakeCtx:
        def __init__(self):
            self.tools, self.hooks, self.commands = {}, {}, {}
            self.context_engine = None
            self.skill = None

        def register_tool(self, name=None, handler=None, schema=None, **kw):
            self.tools[name] = handler

        def register_hook(self, name, handler):
            self.hooks[name] = handler

        def register_command(self, name=None, handler=None, **kw):
            self.commands[name] = handler

        def register_context_engine(self, engine):
            self.context_engine = engine

        def register_skill(self, name, root=None, **kw):
            self.skill = name

    ctx = FakeCtx()
    entry.register(ctx, engine_factory=lambda: eng)
    return ctx, eng


@pytest.fixture
def schema(dsn, request):
    """Fresh throwaway schema per test (engine bootstraps into it), dropped
    afterwards. Pools are closed AFTER drop so cached connections pinned to
    the dropped search_path can never leak into a later test."""
    import uuid
    name = "lcm2_test_" + uuid.uuid4().hex[:10]
    with pgconn.connect(dsn, "public", ddl=True) as cx:
        cx.execute(f'CREATE SCHEMA "{name}"')
    request.node._lcm2_schema = name
    yield name
    try:
        pgconn.shutdown_pools()
    finally:
        with pgconn.connect(dsn, "public", ddl=True) as cx:
            cx.execute(f'DROP SCHEMA "{name}" CASCADE')
        pgconn.shutdown_pools()
