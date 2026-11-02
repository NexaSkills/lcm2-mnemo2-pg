"""R8 — concurrency: N parallel writers into two private schemas + shared.

Threads hammer remember/shared_remember/batch concurrently; afterwards no
writes are lost (every row is present), and a batch either fully applies
or fully rolls back under contention (single PG transaction).

Each writer thread registers its OWN plugin ctx pointing at its OWN
private schema via the thread's env — env is global in-process, so the
threads pin MNEMO2_SCHEMA around their whole run (writer-local schemas,
no cross-thread env races on a shared handler set).
"""
import json
import os
import threading
import uuid

import pytest

import tests.mnemo2.conftest as _c  # qualified: never the sibling suite's conftest

N = 8  # writers per agent


def _mk(dsn, b, sh, request):
    import pgconn
    with pgconn.connect(dsn, "public", ddl=True) as cx:
        cx.execute(f'CREATE SCHEMA "{b}"')
        cx.execute(f'CREATE SCHEMA "{sh}"')

    def _drop():
        with pgconn.connect(dsn, "public", ddl=True) as cx:
            for s in (b, sh):
                cx.execute(f'DROP SCHEMA IF EXISTS "{s}" CASCADE')
    request.addfinalizer(_drop)


def _writer(dsn, priv, agent, errors):
    """One writer: pins MNEMO2_SCHEMA to its schema for its whole run,
    registers its own ctx, writes N rows. The engine resolves schema
    per-call from env, so the pin must span the write loop."""
    prev = os.environ.get("MNEMO2_SCHEMA")
    os.environ["MNEMO2_SCHEMA"] = priv
    try:
        ctx = register_plugin(dsn, priv=priv)[1]
        for i in range(N):
            tag = f"conc-{agent}-{i}"
            r = ctx.tools["mnemosyne_remember"]["handler"](
                {"content": f"concurrent write {tag} " + uuid.uuid4().hex[:6],
                 "scope": "global"})
            if not (r.get("id") or r.get("memory_id") or r.get("ok")):
                errors.append(f"{agent}#{i}: {r}")
    except Exception as e:  # noqa: BLE001
        errors.append(f"{agent} thread died: {e!r}")
    finally:
        if prev is None:
            os.environ.pop("MNEMO2_SCHEMA", None)
        else:
            os.environ["MNEMO2_SCHEMA"] = prev


def test_parallel_writers_no_lost_writes(dsn, priv_schema, request):
    import pgconn
    b = "mnemo2_test_" + uuid.uuid4().hex[:10]
    sh = "shared_test_" + uuid.uuid4().hex[:10]
    _mk(dsn, b, sh, request)

    errors: list[str] = []
    threads = [threading.Thread(target=_writer,
                                args=(dsn, priv_schema, "a", errors)),
               threading.Thread(target=_writer, args=(dsn, b, "b", errors))]
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
    _mk(dsn, b, sh, request)
    errors: list[str] = []

    def sh_write(priv, tag):
        # own ctx + shared-schema pin spanning the loop (env is global;
        # the pin must cover EVERY write, not just registration)
        prev = os.environ.get("MNEMO2_SHARED_SCHEMA")
        os.environ["MNEMO2_SHARED_SCHEMA"] = sh
        try:
            ctx = register_plugin(dsn, priv=priv, shared=sh)[1]
            for i in range(N):
                r = ctx.tools["mnemosyne_shared_remember"]["handler"](
                    {"content": f"shared conc-{tag}-{i}"})
                if not (r.get("id") or r.get("memory_id") or r.get("ok")):
                    errors.append(f"{tag}#{i}: {r}")
        except Exception as e:  # noqa: BLE001
            errors.append(f"{tag} died: {e!r}")
        finally:
            if prev is None:
                os.environ.pop("MNEMO2_SHARED_SCHEMA", None)
            else:
                os.environ["MNEMO2_SHARED_SCHEMA"] = prev

    # R8/P3L: the two writers OVERLAP their pins by design (staggered
    # starts widen neither: env is global, so ANY overlap is the race
    # under test). The old join-first-then-start sequencing serialized
    # the writers — the test verified nothing (green while a proven
    # misrouting bug lived in the product). Concurrent starts are the
    # point; a serialized version must be considered red.
    threads = [threading.Thread(target=sh_write, args=(priv_schema, "a")),
               threading.Thread(target=sh_write, args=(b, "b"))]
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
