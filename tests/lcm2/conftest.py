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
    # exactly this). Sweep classes are throwaway by construction; profile
    # schemas (lcm2_lcm2agent/lcm2_mnemo2agent/shared) never match.
    import re
    _family = re.compile(r"^(lcm2_test_.*|lcm2_probe.*|probe_.*|lcm2_dbg_.*)$")
    with pgconn.connect(BASE_DSN, "public", ddl=True) as cx:
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
