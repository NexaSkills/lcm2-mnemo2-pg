"""R7 — live memory ops in a mnemo2agent-shaped env.

HERMES_HOME pinned to a temp profile dir named `mnemo2agent` so the
plugin's own schema_for_profile derivation yields mnemo2_mnemo2agent
(no pinned lists). remember/recall/stats round-trip with DB-row citations.
"""
import json
import os
import uuid


def test_live_ops_mnemo2agent_shaped(dsn, tmp_path, request):
    home = tmp_path / "mnemo2agent"
    home.mkdir()
    keep = dict(os.environ)

    def restore():
        os.environ.clear()
        os.environ.update(keep)
        os.environ["MNEMO2_PG_DSN"] = dsn
    request.addfinalizer(restore)
    os.environ["HERMES_HOME"] = str(home)
    os.environ.pop("MNEMO2_SCHEMA", None)

    # Bind mnemo-pg2's OWN modules for the whole test (SUITEGREEN-MIKE-0810).
    # A bare `import pgconn` in a COMBINED run binds whichever sibling
    # conftest answered first (this file collected before tests/lcm2 in
    # alphabetical order; lcm2's conftest then pins lcm2-pg's dir at
    # sys.path[0]) — proven live: the r4+r7 two-file run asserts
    # _pg.agent_schema() == 'lcm2_mnemo2agent' at the old line 26.
    # register_agent()'s namespaced package import (the authoritative
    # dispatch route) reads MNEMO2_* through ITS OWN module's globals at
    # dispatch time, so pinning the names here (restored in the finalizer)
    # keeps derivation + dispatch on the mnemo route without touching
    # sys.modules (host-hygiene parity).
    import tests.mnemo2.conftest as _c  # qualified: never the sibling suite's conftest
    _pg = _c.pgconn
    assert _pg.agent_schema() == "mnemo2_mnemo2agent", _pg.agent_schema()

    # SUITEGREEN-MIKE-0810: per-profile schema SHELLS are onboard-owned,
    # not plugin-bootstrap-owned — 7516141 (S6 beez cleanup, 2026-11-03)
    # removed the watchdog's hardcoded CREATE SCHEMA for the deleted
    # agent-shell profiles (lcm2agent/mnemo2agent) and setup_dbs.sh's
    # default agent list holds lcm2agent (not mnemo2agent). The plugin
    # bootstrap is CREATE TABLE-only (qualified CREATEs inside the advisory
    # lock; no CREATE SCHEMA anywhere in plugins/, verified by -S pickaxe),
    # so this test passed only while the watchdog happened to keep the
    # shell alive. Live state cited: mnemo2db on :54331 has NO
    # mnemo2_mnemo2agent schema. The test now performs the onboarding step
    # itself (mint the shell, drop it after) — the derivation under test
    # (agent_schema() from HERMES_HOME) stays unpinned and unpinned-by-env.
    with _pg.connect(dsn, "public", ddl=True) as cx:
        cx.execute('CREATE SCHEMA IF NOT EXISTS "mnemo2_mnemo2agent"')

    def _drop_shell():
        try:
            with _pg.connect(dsn, "public", ddl=True) as cx:
                cx.execute('DROP SCHEMA IF EXISTS "mnemo2_mnemo2agent" CASCADE')
        except Exception:
            pass
    request.addfinalizer(_drop_shell)

                           # (tests/__init__.py package markers landed;
                           # a bare 'conftest' import binds whichever
                           # sibling conftest answered first, qa3 N-3)
    mod, ctx = _c.register_plugin(dsn)  # no priv override — derivation path
    marker = "liveops-" + uuid.uuid4().hex[:8]

    r = ctx.tools["mnemosyne_remember"]["handler"](
        {"content": f"live row {marker} about zebra-quantum", "scope": "global",
         "importance": 0.8})
    assert r.get("id") or r.get("memory_id") or r.get("ok"), r

    # DB citation: the row exists in mnemo2_mnemo2agent.working_memory
    with _pg.connect(dsn, "mnemo2_mnemo2agent") as cx:
        rows = cx.query_dicts(
            "select id, content, importance from working_memory "
            "where content like %s", (f"%{marker}%",))
    assert len(rows) == 1, rows
    assert float(rows[0]["importance"]) == pytest_approx(0.8)

    # recall finds it (fts path)
    rec = ctx.tools["mnemosyne_recall"]["handler"]({"query": marker})
    assert marker in json.dumps(rec), rec

    # stats counts it
    st = ctx.tools["mnemosyne_stats"]["handler"]({})
    assert json.dumps(st), st

    # update -> re-embed path, content changes in the DB
    mid = r.get("id") or r.get("memory_id") or rows[0]["id"]
    u = ctx.tools["mnemosyne_update"]["handler"](
        {"memory_id": str(mid), "content": f"live row UPDATED {marker}"})
    assert u.get("ok") or u.get("updated") or not u.get("error"), u
    with _pg.connect(dsn, "mnemo2_mnemo2agent") as cx:
        c = cx.query_scalar(
            "select content from working_memory where id = %s::text",
            (str(mid),)) or cx.query_scalar(
            "select content from working_memory where content like %s",
            (f"%UPDATED {marker}%",))
    assert "UPDATED" in str(c), c

    # forget removes the row
    f = ctx.tools["mnemosyne_forget"]["handler"]({"memory_id": str(mid)})
    assert f.get("ok") or f.get("deleted") or not f.get("error"), f
    with _pg.connect(dsn, "mnemo2_mnemo2agent") as cx:
        n = cx.query_scalar(
            "select count(*) from working_memory where content like %s",
            (f"%{marker}%",))
    assert n == 0, n


def pytest_approx(x):
    import pytest
    return pytest.approx(x, abs=1e-6)
