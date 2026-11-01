"""R7: real compression cycle persisted in the agent's own schema.

Ingest a session of long messages past the threshold -> engine compresses ->
summary_nodes rows + messages still intact (lossless: every message remains
in the private schema and expandable through the DAG).
"""
import json
import os

import pytest

import pgconn


@pytest.fixture
def engine(schema):
    os.environ["LCM2_DISABLE_MODEL"] = "1"
    os.environ["LCM_CONTEXT_THRESHOLD"] = "0.01"  # compress eagerly for the test
    import engine as E
    return E.LCM2Engine(dsn=pgconn.default_dsn(), schema=schema)


def _ingest(engine, sid, n=40):
    for i in range(n):
        engine.ingest(sid, "user" if i % 2 else "assistant",
                      f"message number {i}: " + ("lorem ipsum dolor sit amet " * 60))
    return n


def test_ingest_persists_messages(engine, schema):
    sid = "s-ingest"
    _ingest(engine, sid, 10)
    with pgconn.connect(pgconn.default_dsn(), schema) as cx:
        rows = cx.query("select count(*) from messages where session_id=%s", (sid,))
    assert rows[0][0] == 10


def test_compression_creates_summary_nodes(engine, schema):
    sid = "s-compress"
    _ingest(engine, sid)
    stats = engine.maybe_compress(sid)
    assert stats.get("compressed"), stats
    with pgconn.connect(pgconn.default_dsn(), schema) as cx:
        nodes = cx.query(
            "select node_id, depth, token_count from summary_nodes "
            "where session_id=%s", (sid,))
    assert nodes, "no summary_nodes rows for compressed session"
    # losslessness: every original message survives after compression
    with pgconn.connect(pgconn.default_dsn(), schema) as cx:
        msgs = cx.query("select count(*) from messages where session_id=%s", (sid,))[0][0]
    assert msgs == 40


def test_expand_recovers_leaf_content_from_summary(engine, schema):
    sid = "s-expand"
    _ingest(engine, sid)
    stats = engine.maybe_compress(sid)
    assert stats.get("compressed"), stats
    with pgconn.connect(pgconn.default_dsn(), schema) as cx:
        node = cx.query(
            "select node_id from summary_nodes where session_id=%s "
            "order by depth desc, node_id limit 1", (sid,))
    assert node
    out = json.loads(engine.tools_lcm_expand({"node_id": node[0][0]}))
    assert out.get("sources") or out.get("content"), out


def test_grep_finds_compressed_content(engine, schema):
    sid = "s-grep"
    _ingest(engine, sid, 12)
    stats = engine.maybe_compress(sid)
    assert stats.get("compressed"), ("compression did not trigger for 12 "
                                     "long messages", stats)
    # "current" scope resolves via HERMES_SESSION_ID / args.session_id; set
    # the session env exactly as the Hermes host does at runtime.
    import unittest.mock as _m
    with _m.patch.dict(os.environ, {"HERMES_SESSION_ID": sid}):
        out = json.loads(engine.tools_lcm_grep({"query": "lorem", "session_scope": "current"}))
    hits = out.get("results") or out.get("hits") or []
    assert hits, out
