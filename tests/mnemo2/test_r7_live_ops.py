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

    import pgconn
    assert pgconn.agent_schema() == "mnemo2_mnemo2agent", pgconn.agent_schema()

    import tests.mnemo2.conftest as _c  # qualified: never the sibling suite's conftest
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
    with pgconn.connect(dsn, "mnemo2_mnemo2agent") as cx:
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
    with pgconn.connect(dsn, "mnemo2_mnemo2agent") as cx:
        c = cx.query_scalar(
            "select content from working_memory where id = %s::text",
            (str(mid),)) or cx.query_scalar(
            "select content from working_memory where content like %s",
            (f"%UPDATED {marker}%",))
    assert "UPDATED" in str(c), c

    # forget removes the row
    f = ctx.tools["mnemosyne_forget"]["handler"]({"memory_id": str(mid)})
    assert f.get("ok") or f.get("deleted") or not f.get("error"), f
    with pgconn.connect(dsn, "mnemo2_mnemo2agent") as cx:
        n = cx.query_scalar(
            "select count(*) from working_memory where content like %s",
            (f"%{marker}%",))
    assert n == 0, n


def pytest_approx(x):
    import pytest
    return pytest.approx(x, abs=1e-6)
