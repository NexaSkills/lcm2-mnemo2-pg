"""Shared fixtures for mnemo-pg2 tests (mnemo2 suite).

Everything runs against the REAL dedicated instance (socket
~/lcm2-mnemo2-pg/pg/run, port 54331, db mnemo2db) over the SAME connection
path the plugin uses in production. No embedded DB anywhere (R3): if the
instance is down the suite fails, it never falls back.

ENGINE PIN (R5 correctness): the plugin's handlers build a FRESH
Engine() per dispatch (vendor parity), resolving schema/shared schema
from the ENVIRONMENT at call time. A test that registers agent B with a
different private/shared schema than agent A therefore MUST NOT let A's
handlers run while B's env is in place (and must not rely on env state
surviving register()). register_agent() below snapshots the env the
plugin was registered with and re-applies it around every handler call —
deterministic multi-agent tests, zero reliance on ambient env.
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

# Module keys this file creates in sys.modules (host-leak guard).
_OWNED_KEYS: set[str] = set()


def _own(key, obj):
    _OWNED_KEYS.add(key)
    sys.modules[key] = obj
    return obj


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
    _family = re.compile(r"^(mnemo2_test_.*|mnemo2_probe.*|shared_test_.*|probe_.*)$")
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
    _drop_schemas(dsn, [name])


def _drop_schemas(dsn, names):
    try:
        with pgconn.connect(dsn, "public", ddl=True) as cx:
            for s in names:
                cx.execute(f'DROP SCHEMA IF EXISTS "{s}" CASCADE')
    except Exception:
        pass


@pytest.fixture
def scratch_schemas(dsn):
    """Extra throwaway schemas on demand: mk() returns fresh names, all
    dropped at teardown via request-independent finalizer list."""
    import uuid
    made = []

    def mk(prefix="mnemo2_test"):
        name = f"{prefix}_" + uuid.uuid4().hex[:10]
        with pgconn.connect(dsn, "public", ddl=True) as cx:
            cx.execute(f'CREATE SCHEMA "{name}"')
        made.append(name)
        return name

    yield mk
    _drop_schemas(dsn, made)


class FakeCtx:
    """Host-plugin-context double: records register_tool / register_hook /
    register_context_engine / register_command calls. Tool handlers are
    env-pinned wrappers (see module docstring ENGINE PIN)."""

    def __init__(self, env_snapshot=None):
        self.tools, self.hooks, self.commands = {}, {}, {}
        self.context_engine = None
        self._env = env_snapshot or dict(os.environ)

    def _pin(self, fn):
        env = self._env

        def wrapped(*a, **kw):
            # R8 (P3L): NEVER os.environ.clear()+update() around a handler
            # call. Two agents' pins interleave and the environ snapshot
            # read (`dict(os.environ)`, a C iteration) lands inside the
            # rival's clear window -> KeyError('SHELL'/'INPUT_METHOD'...)
            # from <frozen os> __getitem__ inside conftest. Apply the
            # snapshot as SETs + targeted POPs (a key is only ever absent
            # for microseconds, never gone from the C array), and restore
            # the same way. Global-env mutation is the very race this
            # lane is meant to expose in the PRODUCT, not to manufacture
            # in the harness.
            prev = None
            try:
                prev = os.environ.copy()
                for k, v in env.items():
                    if os.environ.get(k) != v:
                        os.environ[k] = v
                for k in prev.keys() - env.keys():
                    os.environ.pop(k, None)
            except Exception:
                # env-less host (import-time failure path): run unpinned
                prev = None
            try:
                return fn(*a, **kw)
            finally:
                if prev is not None:
                    for k, v in prev.items():
                        if os.environ.get(k) != v:
                            os.environ[k] = v
                    for k in os.environ.keys() - prev.keys():
                        os.environ.pop(k, None)
        return wrapped

    def register_tool(self, *, name, toolset, schema, handler, description=""):
        # HOST CONTRACT PARITY (tools/registry.py::_normalize_handler_result
        # and the vendor tools): a handler returns a JSON STRING; callers
        # want dicts. Tests call handlers with `.get(...)` everywhere, so
        # the recorded handler dict-izes a JSON-string result (mirroring
        # what the host's registry dispatch does before the model sees
        # it). A dict result is passed through untouched.
        inner = self._pin(handler)

        def handler_dict(args=None, **kw):
            r = inner(args, **kw) if args is not None else inner(**kw)
            if isinstance(r, str):
                import json as _json
                try:
                    return _json.loads(r)
                except Exception:
                    return {"ok": True, "raw": r}
            return r
        self.tools[name] = {"toolset": toolset, "schema": schema,
                            "handler": handler_dict,
                            "raw_handler": self._pin(handler),
                            "description": description}

    def register_hook(self, event, fn):
        self.hooks.setdefault(event, []).append(self._pin(fn))

    def register_command(self, name, handler, description=""):
        self.commands[name] = handler

    def register_context_engine(self, engine):
        self.context_engine = engine


@pytest.fixture
def fake_ctx():
    return FakeCtx()


def register_agent(dsn, priv=None, shared=None):
    """Register mnemo-pg2 through ITS OWN package import path exactly the
    host does (spec_from_file_location on __init__.py with submodule
    search locations). The env the plugin loads under is SNAPSHOTTED and
    re-applied around every recorded tool/hook call (FakeCtx._pin), so
    several agents coexist deterministically in one process.
    Returns FakeCtx with .tools/.hooks."""
    import importlib.util
    name = "mnemo_pg2_probe_pkg"
    init = pathlib.Path(PLUGIN_DIR) / "__init__.py"
    prev = dict(os.environ)
    os.environ["MNEMO2_PG_DSN"] = dsn
    if priv:
        os.environ["MNEMO2_SCHEMA"] = priv
    else:
        os.environ.pop("MNEMO2_SCHEMA", None)
    if shared:
        os.environ["MNEMO2_SHARED_SCHEMA"] = shared
    else:
        os.environ.pop("MNEMO2_SHARED_SCHEMA", None)
    ctx = FakeCtx(env_snapshot=dict(os.environ))
    spec = importlib.util.spec_from_file_location(
        name, init, submodule_search_locations=[PLUGIN_DIR])
    try:
        mod = importlib.util.module_from_spec(spec)
        mod.__package__ = name
        mod.__path__ = [PLUGIN_DIR]
        _own(name, mod)
        spec.loader.exec_module(mod)
        assert hasattr(mod, "register"), (
            "mnemo-pg2 __init__ must expose register() "
            "(mnemo2agent boot-log defect: 'no register() function')")
        mod.register(ctx)
    finally:
        # R8 (P3L): same as FakeCtx._pin — rebind/pop, never a global
        # clear(): a rival thread's snapshot read inside the clear window
        # KeyErrors from <frozen os>.
        for k, v in prev.items():
            if os.environ.get(k) != v:
                os.environ[k] = v
        for k in list(os.environ):
            if k not in prev:
                os.environ.pop(k, None)
    return ctx


def register_plugin(dsn, priv=None, shared=None, fake=None):
    """Back-compat shim: returns (module_placeholder, ctx). Prefer
    register_agent()."""
    ctx = register_agent(dsn, priv=priv, shared=shared)
    return sys.modules["mnemo_pg2_probe_pkg"], ctx


@pytest.fixture
def agent_factory(dsn):
    """register_agent bound to the suite dsn, with auto-unregister at test
    teardown (drop any module the probe package leaves + env untouched)."""
    made = []

    def make(priv=None, shared=None):
        ctx = register_agent(dsn, priv=priv, shared=shared)
        made.append(ctx)
        return ctx
    yield make
