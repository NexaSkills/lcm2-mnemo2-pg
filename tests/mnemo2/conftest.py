"""Shared fixtures for mnemo-pg2 tests (mnemo2 suite).

Everything runs against the REAL dedicated instance (socket
~/lcm2-mnemo2-pg/pg/run, port 54331, db mnemo2db) over the SAME connection
path the plugin uses in production. No embedded DB anywhere (R3): if the
instance is down the suite fails, it never falls back.
"""
import os
import re
import sys
import pathlib

import pytest

REPO = pathlib.Path(__file__).resolve().parents[2]
PLUGIN_DIR = str(REPO / "plugins" / "mnemo-pg2")
sys.path.insert(0, PLUGIN_DIR)

import pgconn  # noqa: E402  (plugin module)

SOCKET = os.environ.get(
    "MNEMO2_PG_HOST", os.path.expanduser("~/lcm2-mnemo2-pg/pg/run"))
DB = "mnemo2db"
BASE_DSN = f"dbname={DB} host={SOCKET} port=54331 user=mnemo201"

# Pin the plugin's default DSN + determinism (hashed embeddings, no network).
os.environ["MNEMO2_PG_DSN"] = BASE_DSN
os.environ.setdefault("MNEMO2_EMBED_MODEL", "hashed")


@pytest.fixture(scope="session")
def dsn():
    pgconn.shutdown_pools()
    try:
        with pgconn.connect(BASE_DSN, "public") as cx:
            assert cx.query_scalar("select 1") == 1
    except Exception as e:
        pytest.fail(f"mnemo2 instance unreachable at {SOCKET}:54331 db={DB}: "
                    f"{e} (start scripts/ensure_instance.sh; never falls back, R3)")
    # Sweep throwaway schemas LEFT BY PREVIOUS runs before this one mints
    # its own (phase-2 lesson: a killed run skips teardown; residue then
    # false-fails a later run's tripwire). Profile schemas
    # (mnemo2_mnemo2agent/mnemo2_lcm2agent/shared) never match the family.
    _family = re.compile(r"^(mnemo2_test_.*|mnemo2_probe.*|shared_test_.*)$")
    with pgconn.connect(BASE_DSN, "public", ddl=True) as cx:
        stale = [r["nspname"] for r in cx.query_dicts(
            "select nspname from pg_namespace")
            if _family.match(r["nspname"])]
        for n in stale:
            cx.execute(f'DROP SCHEMA IF EXISTS "{n}" CASCADE')
    return BASE_DSN


@pytest.fixture
def priv_schema(dsn):
    """Fresh throwaway PRIVATE schema (mnemo2_test_<uuid>), dropped after.
    Bootstrap DDL runs lazily through the plugin's own connect(ddl=True)."""
    import uuid
    name = "mnemo2_test_" + uuid.uuid4().hex[:10]
    with pgconn.connect(dsn, "public", ddl=True) as cx:
        cx.execute(f'CREATE SCHEMA "{name}"')
    yield name
    with pgconn.connect(dsn, "public", ddl=True) as cx:
        cx.execute(f'DROP SCHEMA IF EXISTS "{name}" CASCADE')


class FakeCtx:
    """Host-plugin-context double: records register_tool / register_hook /
    register_context_engine / register_command calls."""

    def __init__(self):
        self.tools, self.hooks, self.commands = {}, {}, {}
        self.context_engine = None

    def register_tool(self, *, name, toolset, schema, handler, description=""):
        self.tools[name] = {"toolset": toolset, "schema": schema,
                            "handler": handler, "description": description}

    def register_hook(self, event, fn):
        self.hooks.setdefault(event, []).append(fn)

    def register_command(self, name, handler, description=""):
        self.commands[name] = handler

    def register_context_engine(self, engine):
        self.context_engine = engine


@pytest.fixture
def fake_ctx():
    return FakeCtx()


def register_plugin(dsn, priv=None, shared=None, fake=None):
    """Register mnemo-pg2 through ITS OWN package import path exactly the
    host does (spec_from_file_location on __init__.py with submodule search
    locations) into `fake`. Returns (module, ctx). Env is restored."""
    import importlib.util
    fake = fake or FakeCtx()
    name = "hermes_plugins_test_mnemo_pg2"
    init = pathlib.Path(PLUGIN_DIR) / "__init__.py"
    spec = importlib.util.spec_from_file_location(
        name, init, submodule_search_locations=[PLUGIN_DIR])
    mod = importlib.util.module_from_spec(spec)
    mod.__package__ = name
    mod.__path__ = [PLUGIN_DIR]
    sys.modules[name] = mod
    prev_priv, prev_shared = (os.environ.get("MNEMO2_SCHEMA"),
                              os.environ.get("MNEMO2_SHARED_SCHEMA"))
    os.environ["MNEMO2_PG_DSN"] = dsn
    if priv:
        os.environ["MNEMO2_SCHEMA"] = priv
    if shared:
        os.environ["MNEMO2_SHARED_SCHEMA"] = shared
    try:
        spec.loader.exec_module(mod)
        assert hasattr(mod, "register"), (
            "mnemo-pg2 __init__ must expose register() "
            "(mnemo2agent boot-log defect: 'no register() function')")
        mod.register(fake)
    finally:
        if prev_priv is None:
            os.environ.pop("MNEMO2_SCHEMA", None)
        else:
            os.environ["MNEMO2_SCHEMA"] = prev_priv
        if prev_shared is None:
            os.environ.pop("MNEMO2_SHARED_SCHEMA", None)
        else:
            os.environ["MNEMO2_SHARED_SCHEMA"] = prev_shared
    return mod, fake
