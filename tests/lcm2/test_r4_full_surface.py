"""R4-full: EVERY vendor lcm tool (spec-phase0 A1 wildcard enumeration)
exercised FOR REAL against the DB — not just "returns JSON".

Enumeration source (wildcard rule, per docs/spec-phase2b.md §B1):
  - schemas.py top-level `"name": "lcm_..."` dicts
  - engine.py handle_tool_call dispatch-map keys
Union must equal what register() exposes; drift fails the test.

Each tool then gets a functional probe against a seeded throwaway schema
(30 messages + compression), proving the real read/write path on lcm2db.
"""
import importlib
import inspect
import json
import pathlib
import re
import sys

import pytest

import pgconn  # noqa: F401  (conftest puts the plugin dir on sys.path)

REPO = pathlib.Path(__file__).resolve().parents[2]
VENDOR_CANDIDATES = [
    pathlib.Path.home() / "lcm-upstream",
    pathlib.Path.home() / "lcm2-recon" / "lcm-upstream",
]

NAME_RX = re.compile(r'"name":\s*"(lcm_[a-z_0-9]+)"')
DISPATCH_RX = re.compile(r'"(lcm_[a-z_0-9]+)":\s*[a-zA-Z_]')


def vendor_source():
    for c in VENDOR_CANDIDATES:
        if (c / "plugin.yaml").is_file():
            return c
    pytest.skip("vendor hermes-lcm checkout not found")


def vendor_tool_names():
    src = vendor_source()
    names = set()
    for p in src.rglob("*.py"):
        if "tests" in p.parts or "__pycache__" in p.parts:
            continue
        names.update(NAME_RX.findall(p.read_text(errors="replace")))
    names.update(DISPATCH_RX.findall((src / "engine.py").read_text()))
    return names


# ---------------------------------------------------------------- fixtures

MARKER = "zephyrq"          # distinctive tsvector-friendly token
SESSION = "s-r4full"


@pytest.fixture
def seeded(registered):
    """Registered plugin surface + a compressed session in a real schema.

    Every table in the freshly bootstrapped schema is EMPTY at fixture
    entry (asserted): schema is per-test (uuid) and pools are closed
    between tests, so a row visible here would mean pool leakage — fail
    loudly instead of silently mixing probes.
    """
    ctx, eng = registered
    with pgconn.connect(eng.dsn, eng.schema) as cx:
        for tbl in ("messages", "summary_nodes", "lcm_assertions"):
            leftover = cx.query_scalar(f"select count(*) from {tbl}")
            assert leftover == 0, f"leakage: {tbl} has {leftover} rows pre-seed"
    for i in range(30):
        role = "user" if i % 2 else "assistant"
        content = (f"message {i} about the zephyrq protocol entry number {i}, "
                   f"with figure {1000 + i} and topic {'alpha' if i % 3 else 'beta'}")
        eng.ingest(SESSION, role, content)
    eng.maybe_compress(SESSION)
    with pgconn.connect(eng.dsn, eng.schema) as cx:
        n_nodes = cx.query_scalar("select count(*) from summary_nodes")
    assert n_nodes >= 1, "compression must produce leaf summaries for R4-full"
    with pgconn.connect(eng.dsn, eng.schema) as cx:
        store_first = cx.query_scalar(
            "select min(store_id) from messages where session_id=%s", (SESSION,))
        node_leaf = cx.query_scalar(
            "select min(node_id) from summary_nodes where depth=0")
        node_any = cx.query_scalar("select min(node_id) from summary_nodes")
    return ctx, eng, {"store": store_first, "leaf": node_leaf,
                      "node": node_any}


def call(ctx, name, args):
    doc = json.loads(ctx.tools[name](args))
    assert isinstance(doc, dict) and doc, name
    assert doc.get("implementation") == "lcm2-pg/native", name
    return doc


# ------------------------------------------------- enumeration parity tests

def test_vendor_wildcard_enumeration_is_documented():
    names = vendor_tool_names()
    assert len(names) >= 15, names
    # A1's 15 provides_tools must be a subset of the wildcard union
    from test_r4_tools_surface import VENDOR_TOOLS
    assert set(VENDOR_TOOLS) <= names, set(VENDOR_TOOLS) - names


def test_register_matches_vendor_wildcard_union(registered):
    ctx, _ = registered
    names = vendor_tool_names()
    assert set(ctx.tools) == names, (
        f"registered={sorted(ctx.tools)} vendor={sorted(names)}")


# ----------------------------------------------------- functional per-tool

def test_grep_finds_seeded_marker(registered, seeded):
    ctx, eng, _ = seeded
    doc = call(ctx, "lcm_grep", {"query": MARKER, "session_scope": "all"})
    hits = doc.get("results", []) + doc.get("summary_nodes", [])
    assert hits, "marker text must be findable"


def test_recall_finds_seeded_content(registered, seeded):
    ctx, eng, _ = seeded
    doc = call(ctx, "lcm_recall", {"query": "zephyrq protocol"})
    assert doc.get("hits") or doc.get("summaries")


def test_recent_window_shape(registered, seeded):
    ctx, eng, _ = seeded
    doc = call(ctx, "lcm_recent", {"period": "today"})
    assert "ready_rollups" in doc and doc.get("period")


def test_load_session_pages_seeded_messages(registered, seeded):
    ctx, eng, _ = seeded
    doc = call(ctx, "lcm_load_session",
               {"session_id": SESSION, "limit": 10})
    assert doc["count"] == 10 and doc["messages"][0]["session_id"] == SESSION
    assert doc["next_cursor"]
    doc2 = call(ctx, "lcm_load_session",
                {"session_id": SESSION, "limit": 10,
                 "after_store_id": doc["next_cursor"]})
    assert all(m["store_id"] > doc["next_cursor"] for m in doc2["messages"])


def test_expand_store_returns_exact_content(registered, seeded):
    ctx, eng, ids = seeded
    doc = call(ctx, "lcm_expand", {"store_id": ids["store"]})
    assert doc["kind"] == "message" and MARKER in (doc["content"] or "")


def test_expand_node_descends_dag(registered, seeded):
    ctx, eng, ids = seeded
    doc = call(ctx, "lcm_expand", {"node_id": ids["node"]})
    assert doc["kind"] == "summary" and doc["summary"]
    if ids["node"] == ids["leaf"]:
        assert doc["sources"], "leaf node must expose its source messages"


def test_describe_overview_and_node(registered, seeded):
    ctx, eng, ids = seeded
    doc = call(ctx, "lcm_describe", {})
    assert doc["dag_overview"], "DAG overview non-empty after compression"
    n = call(ctx, "lcm_describe", {"node_id": ids["leaf"]})
    assert n["child_count"] >= 1 and "expand_hint" in n


def test_expand_query_uses_node_context(registered, seeded):
    ctx, eng, ids = seeded
    doc = call(ctx, "lcm_expand_query",
               {"prompt": "what happened?", "node_ids": [ids["node"]]})
    assert doc["context_chars"] > 0


def test_query_state_reads_real_assertion(registered, seeded):
    ctx, eng, _ = seeded
    with pgconn.connect(eng.dsn, eng.schema) as cx:
        cx.execute(
            "insert into lcm_assertions(subject_key,predicate_key,object_value,"
            "kind,scope_key,valid_at) values"
            "('user:self','lives_in','Stellenbosch','fact','global',"
            "now() - interval '2 hours')")
    doc = call(ctx, "lcm_query_state", {"subject_key": "user:self"})
    assert doc["assertions"] and \
        doc["assertions"][0]["object"] == "Stellenbosch"
    asof = call(ctx, "lcm_query_state",
                {"subject_key": "user:self",
                 "as_of": __import__("time").time() + 60})
    assert asof["assertions"]
    kinds = call(ctx, "lcm_query_state",
                 {"subject_key": "user:self", "kinds": ["preference"]})
    assert kinds["assertions"] == []


def test_compute_counts_and_sums(registered, seeded):
    ctx, eng, _ = seeded
    ops = [{"value": 3}, {"value": 4}]
    doc = call(ctx, "lcm_compute", {"question": "how many?", "operands": ops})
    assert doc["status"] == "computed" and doc["result"] == 2
    doc = call(ctx, "lcm_compute",
               {"question": "sum the values", "operands": ops})
    assert doc["status"] == "computed" and doc["result"] == 7


def test_compile_evidence_echoes_refs(registered, seeded):
    ctx, eng, ids = seeded
    refs = [f"lcm_expand(store_id={ids['store']})"]
    doc = call(ctx, "lcm_compile_evidence",
               {"question": "x?", "baseline_refs": refs})
    assert doc["ref_count"] == 1


def test_evidence_pack_hydrates_real_ref(registered, seeded):
    ctx, eng, ids = seeded
    doc = call(ctx, "lcm_evidence_pack",
               {"question": "x?",
                "baseline_refs": [f"lcm_expand(store_id={ids['store']})"]})
    assert doc["refs"] and MARKER in doc["refs"][0].get("content", "")


def test_retrieve_episode_lifecycle(registered, seeded):
    ctx, eng, _ = seeded
    st = call(ctx, "lcm_retrieve", {"action": "start",
                                    "requirements": [{"slot_id": "s1"}]})
    rid = st["retrieval_id"]
    for _ in range(3):
        d = call(ctx, "lcm_retrieve", {"action": "search",
                                       "retrieval_id": rid, "tool": "lcm_grep",
                                       "tool_args": {"query": "x"}})
        assert d["status"] == "dispatch"
    over = call(ctx, "lcm_retrieve", {"action": "search",
                                      "retrieval_id": rid, "tool": "lcm_grep",
                                      "tool_args": {}})
    assert over["status"] == "rejected", "3-search budget is the contract"
    fin = call(ctx, "lcm_retrieve", {"action": "finish", "retrieval_id": rid,
                                     "selected_refs": []})
    assert fin["status"] == "finished"
    # episode state must have been DB-backed (survives stateless hosts)
    with pgconn.connect(eng.dsn, eng.schema) as cx:
        left = cx.query_scalar(
            "select count(*) from lcm_trajectory_states where state_key like"
            " 'retrieve_episode:%'")
    assert left == 0, "finish/abandon must clean up persisted episodes"


def test_status_counts_reflect_seed(registered, seeded):
    ctx, eng, _ = seeded
    doc = call(ctx, "lcm_status", {})
    assert doc["counts"]["messages"] >= 30
    assert doc["counts"]["summary_nodes"] >= 1


def test_inspect_shows_session(registered, seeded):
    ctx, eng, _ = seeded
    doc = call(ctx, "lcm_inspect", {})
    assert any(t["session_id"] == SESSION for t in doc["fresh_tail"])
    assert doc["lifecycle"]


def test_doctor_healthy(registered, seeded):
    ctx, eng, _ = seeded
    doc = call(ctx, "lcm_doctor", {})
    assert doc["healthy"] is True and doc["issues"] == []


# ------------------------------------------------------------ extras

def test_slash_command_subcommands(registered, seeded):
    ctx, eng, _ = seeded
    import command
    for sub, key in (("", "implementation"), ("status", "counts"),
                     ("inspect", "fresh_tail"), ("doctor", "healthy"),
                     ("grep zephyrq", "results")):
        doc = json.loads(command.handle(sub, eng))
        assert key in doc, (sub, sorted(doc)[:6])


def test_bundled_skill_registered_and_present(registered):
    ctx, _ = registered
    assert ctx.skill == "lcm2-pg"
    p = pathlib.Path(inspect.getfile(sys.modules["plugin_entry"])).parent
    assert (p / "skills" / "lcm2-pg" / "SKILL.md").is_file()
