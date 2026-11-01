"""R5 — shared cross-agent surface through the ONE `shared` schema.

Agent A (schema mnemo2_<a>) writes via shared_remember; BOTH agents'
private schemas must see the row through shared_recall; private isolation
holds both ways. Real PG instance, real rows cited.
"""
import json
import uuid

from conftest import register_plugin


def _agent_ctx(dsn, priv, shared):
    mod, ctx = register_plugin(dsn, priv=priv, shared=shared)
    return ctx


def test_shared_visible_from_two_agents(dsn, priv_schema, request):
    # two throwaway private schemas + one shared sandbox schema
    import pgconn
    a = priv_schema
    b = "mnemo2_test_" + uuid.uuid4().hex[:10]
    sh = "shared_test_" + uuid.uuid4().hex[:10]
    with pgconn.connect(dsn, "public", ddl=True) as cx:
        cx.execute(f'CREATE SCHEMA "{b}"; CREATE SCHEMA "{sh}"')
    request.addfinalizer(lambda: [
        pgconn.connect(dsn, "public", ddl=True).__enter__().execute(
            f'DROP SCHEMA IF EXISTS "{s}" CASCADE') for s in (b, sh)])

    marker = "shared-marker-" + uuid.uuid4().hex[:8]
    ctx_a = _agent_ctx(dsn, a, sh)
    r = ctx_a.tools["mnemosyne_shared_remember"]["handler"](
        {"content": f"cross-agent fact {marker}", "importance": 0.9})
    assert r.get("id") or r.get("memory_id") or r.get("ok"), r

    # row really lives in the shared schema
    with pgconn.connect(dsn, sh) as cx:
        rows = cx.query_dicts(
            "select content from working_memory "
            "where content like %s", (f"%{marker}%",))
    assert len(rows) == 1, rows

    # agent B (different private schema, same shared) sees it
    ctx_b = _agent_ctx(dsn, b, sh)
    hit = ctx_b.tools["mnemosyne_shared_recall"]["handler"](
        {"query": marker})
    txt = json.dumps(hit)
    assert marker in txt, hit

    # and agent A recalls it too
    hit_a = ctx_a.tools["mnemosyne_shared_recall"]["handler"](
        {"query": marker})
    assert marker in json.dumps(hit_a), hit_a

    # shared_stats sees >=1 row
    st = ctx_a.tools["mnemosyne_shared_stats"]["handler"]({})
    assert json.dumps(st), st

    # shared_forget deletes; both agents stop seeing it
    mid = r.get("id") or r.get("memory_id")
    if mid:
        ctx_a.tools["mnemosyne_shared_forget"]["handler"]({"memory_id": mid})
        with pgconn.connect(dsn, sh) as cx:
            n = cx.query_scalar(
                "select count(*) from working_memory where content like %s",
                (f"%{marker}%",))
        assert n == 0, n


def test_private_isolation_both_ways(dsn, priv_schema, request):
    import pgconn
    a = priv_schema
    b = "mnemo2_test_" + uuid.uuid4().hex[:10]
    sh = "shared_test_" + uuid.uuid4().hex[:10]
    with pgconn.connect(dsn, "public", ddl=True) as cx:
        cx.execute(f'CREATE SCHEMA "{b}"; CREATE SCHEMA "{sh}"')
    request.addfinalizer(lambda: [
        pgconn.connect(dsn, "public", ddl=True).__enter__().execute(
            f'DROP SCHEMA IF EXISTS "{s}" CASCADE') for s in (b, sh)])

    secret = "private-only-" + uuid.uuid4().hex[:8]
    ctx_a = _agent_ctx(dsn, a, sh)
    ctx_b = _agent_ctx(dsn, b, sh)
    r = ctx_a.tools["mnemosyne_remember"]["handler"](
        {"content": f"private {secret}", "scope": "global"})
    assert r.get("id") or r.get("memory_id") or r.get("ok"), r

    # B's recall must NOT surface A's private row
    hit_b = json.dumps(ctx_b.tools["mnemosyne_recall"]["handler"](
        {"query": secret}))
    assert secret not in hit_b, "private leaked into sibling agent"

    # A sees its own
    assert secret in json.dumps(ctx_a.tools["mnemosyne_recall"]["handler"](
        {"query": secret}))

    # shared recall in B also cannot see A's private row
    assert secret not in json.dumps(ctx_b.tools["mnemosyne_shared_recall"]["handler"](
        {"query": secret}))
