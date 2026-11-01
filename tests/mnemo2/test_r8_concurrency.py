"""R8 — concurrency: N parallel writers into two private schemas + shared.

Threads hammer remember/shared_remember/batch concurrently; afterwards no
writes are lost (every row is present), and a batch either fully applies
or fully rolls back under contention (single PG transaction).
"""
import json
import threading
import uuid

import pytest

from conftest import register_plugin

N = 8  # writers per agent


def _writer(ctx, agent, results, errors):
    try:
        for i in range(N):
            tag = f"conc-{agent}-{i}"
            r = ctx.tools["mnemosyne_remember"]["handler"](
                {"content": f"concurrent write {tag} " + uuid.uuid4().hex[:6],
                 "scope": "global"})
            if not (r.get("id") or r.get("memory_id") or r.get("ok")):
                errors.append(f"{agent}#{i}: {r}")
    except Exception as e:  # noqa: BLE001
        errors.append(f"{agent} thread died: {e!r}")


def test_parallel_writers_no_lost_writes(dsn, priv_schema, request):
    import pgconn
    b = "mnemo2_test_" + uuid.uuid4().hex[:10]
    sh = "shared_test_" + uuid.uuid4().hex[:10]
    with pgconn.connect(dsn, "public", ddl=True) as cx:
        cx.execute(f'CREATE SCHEMA "{b}"; CREATE SCHEMA "{sh}"')
    request.addfinalizer(lambda: [
        pgconn.connect(dsn, "public", ddl=True).__enter__().execute(
            f'DROP SCHEMA IF EXISTS "{s}" CASCADE') for s in (b, sh)])

    ctx_a = register_plugin(dsn, priv=priv_schema, shared=sh)[1]
    ctx_b = register_plugin(dsn, priv=b, shared=sh)[1]
    errors: list[str] = []
    threads = [threading.Thread(target=_writer, args=(ctx_a, "a", None, errors)),
               threading.Thread(target=_writer, args=(ctx_b, "b", None, errors))]
    for t in threads:
        t.start()
    for t in threads:
        t.join(120)
    assert not errors, errors

    # every one of the 2N rows is present in ITS private schema
    for schema, agent in ((priv_schema, "a"), (b, "b")):
        with pgconn.connect(dsn, schema) as cx:
            n = cx.query_scalar(
                "select count(*) from working_memory where content like %s",
                (f"%conc-{agent}-%",))
        assert n == N, f"schema {schema}: expected {N} rows, got {n}"


def test_concurrent_shared_surface(dsn, priv_schema, request):
    import pgconn
    b = "mnemo2_test_" + uuid.uuid4().hex[:10]
    sh = "shared_test_" + uuid.uuid4().hex[:10]
    with pgconn.connect(dsn, "public", ddl=True) as cx:
        cx.execute(f'CREATE SCHEMA "{b}"; CREATE SCHEMA "{sh}"')
    request.addfinalizer(lambda: [
        pgconn.connect(dsn, "public", ddl=True).__enter__().execute(
            f'DROP SCHEMA IF EXISTS "{s}" CASCADE') for s in (b, sh)])

    ctx_a = register_plugin(dsn, priv=priv_schema, shared=sh)[1]
    ctx_b = register_plugin(dsn, priv=b, shared=sh)[1]
    errors: list[str] = []

    def sh_write(ctx, tag):
        try:
            for i in range(N):
                r = ctx.tools["mnemosyne_shared_remember"]["handler"](
                    {"content": f"shared conc-{tag}-{i}"})
                if not (r.get("id") or r.get("memory_id") or r.get("ok")):
                    errors.append(f"{tag}#{i}: {r}")
        except Exception as e:  # noqa: BLE001
            errors.append(f"{tag} died: {e!r}")

    threads = [threading.Thread(target=sh_write, args=(ctx_a, "a")),
               threading.Thread(target=sh_write, args=(ctx_b, "b"))]
    for t in threads:
        t.start()
    for t in threads:
        t.join(120)
    assert not errors, errors
    with pgconn.connect(dsn, sh) as cx:
        n = cx.query_scalar(
            "select count(*) from working_memory where content like 'shared conc-%'")
    assert n == 2 * N, n


def test_batch_atomicity_under_contention(dsn, priv_schema):
    """A batch with a poisoned final op must roll back ENTIRELY (one PG
    transaction), while concurrent single writes proceed in parallel."""
    ctx = register_plugin(dsn, priv=priv_schema)[1]
    errors: list[str] = []

    def hammer():
        try:
            for _ in range(6):
                ctx.tools["mnemosyne_remember"]["handler"](
                    {"content": "hammer row " + uuid.uuid4().hex[:6]})
        except Exception as e:  # noqa: BLE001
            errors.append(repr(e))

    ops = [
        {"action": "remember", "content": "batch-atomic-alpha"},
        {"action": "remember", "content": "batch-atomic-beta"},
        {"action": "update", "memory_id": "00000000-0000-0000-0000-000000000000"},
    ]  # poisoned third op (nonexistent id) forces rollback
    t = threading.Thread(target=hammer)
    t.start()
    res = ctx.tools["mnemosyne_batch"]["handler"]({"operations": ops})
    t.join(60)
    assert not errors, errors
    assert res.get("ok") is False or res.get("error") or res.get("rolled_back"), res
    import pgconn
    with pgconn.connect(dsn, priv_schema) as cx:
        n = cx.query_scalar(
            "select count(*) from working_memory "
            "where content like 'batch-atomic-%'")
    assert n == 0, f"batch wrote despite failure: {n}"
