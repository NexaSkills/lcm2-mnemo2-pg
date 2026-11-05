"""THRASH-CURE (beez live 2026-11-05): compress() must hand the host a
REDUCED transcript.

Live defect: LCM2Engine.compress() returned the list UNMODIFIED (store-side
compaction only). The host's post-tool preflight (agent/turn_preflight.py)
compares before/after pressure; zero reduction ->
"Pre-API compression made insufficient progress" -> re-fires EVERY turn once
past threshold (observed 11+ compressions in 11 minutes), heading for
compression_exhausted -> gateway session reset.

Contract proven here (clone-shape; DB calls monkeypatched, no bootstrap):
  - short transcripts (<12 msgs) are returned as-is (nothing to gain)
  - long transcripts shrink: head(system + prior handoffs) + ONE
    [LCM2-PG CONTEXT HANDOFF] user row + verbatim tail
  - a fresh tail with no summary nodes defers (None -> transcript as-is)
  - a tool result kept in the tail never orphans its assistant(tool_calls)
  - awaiting_real_usage_after_compression True only after a real reduction
  - summary nodes whose messages are all covered by a newer node are
    deduped out of the handoff
"""
import importlib.util
import json
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PLUGIN_DIR = os.path.join(REPO, "plugins", "lcm2-pg")
if PLUGIN_DIR not in sys.path:
    sys.path.insert(0, PLUGIN_DIR)


def _load(name):
    full = f"_thrash_{name}"
    mod = sys.modules.get(full)
    if mod is not None:
        return mod
    spec = importlib.util.spec_from_file_location(
        full, os.path.join(PLUGIN_DIR, name + ".py"))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[full] = mod
    spec.loader.exec_module(mod)
    return mod


engine = _load("engine")


class _FakeConn:
    """Serves rows for the two queries _reduce_transcript makes."""

    def __init__(self, nodes, msgs):
        self.nodes, self.msgs = nodes, msgs

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def query(self, sql, params=None):
        if "summary_nodes" in sql:
            return self.nodes
        if "select store_id from messages" in sql:
            want = set(params[0])
            return [(i,) for i in self.msgs if i in want]
        raise AssertionError("unexpected sql: " + sql)


def _mk(nodes, msgs):
    e = engine.LCM2Engine.__new__(engine.LCM2Engine)
    e._pinned_session = "s1"
    e.compression_count = 0
    e.awaiting_real_usage_after_compression = False
    e.protect_first_n, e.protect_last_n = 3, 6
    e.ingest = lambda *a, **k: 0
    e.maybe_compress = lambda sid: {"compressed": False}
    e._conn = lambda ddl=False: _FakeConn(nodes, msgs)
    return e


def _long(n_pairs=16):
    msgs = [{"role": "system", "content": "sys prompt"}]
    for i in range(n_pairs):
        msgs.append({"role": "user", "content": f"question {i} " + "x" * 80})
        msgs.append({"role": "assistant", "content": f"answer {i}"})
    return msgs


def test_short_transcript_unchanged():
    e = _mk([], [])
    msgs = _long(3)
    assert len(msgs) < 12
    assert e._reduce_transcript("s1", msgs) is None


def test_long_transcript_reduces_with_one_handoff():
    msgs = _long()
    nodes = [(7, "- tool: did a thing", json.dumps([10, 11])),
             (8, "- user: asked a thing", json.dumps([12, 13]))]
    e = _mk(nodes, [10, 11, 12, 13])
    out = e._reduce_transcript("s1", msgs)
    assert out is not None and len(out) < len(msgs)
    handoffs = [m for m in out
                if str(m.get("content", "")).startswith(
                    engine.HANDOFF_PREFIX[:40])]
    assert len(handoffs) == 1 and handoffs[0]["role"] == "user"
    body = handoffs[0]["content"]
    assert "[node 7]" in body and "[node 8]" in body
    assert engine.HANDOFF_SUFFIX in body
    # tail rows are verbatim: the newest user question survives whole
    assert any(m.get("role") == "user" and "question 15" in
               str(m.get("content", "")) for m in out)
    # system prompt stays in the head, exactly once
    assert [m["role"] for m in out].count("system") == 1


def test_prior_handoff_moved_to_head_not_rebuilt():
    msgs = _long()
    msgs[1] = {"role": "user", "content": engine.HANDOFF_PREFIX + " [node 1]\n"
               + engine.HANDOFF_SUFFIX}
    nodes = [(9, "- tool: later", json.dumps([20]))]
    e = _mk(nodes, [20])
    out = e._reduce_transcript("s1", msgs)
    assert out is not None
    hand = [i for i, m in enumerate(out)
            if str(m.get("content", "")).startswith(
                engine.HANDOFF_PREFIX[:40])]
    # prior handoff (head) + the new one: exactly two, prior BEFORE new
    assert len(hand) == 2 and hand[0] < hand[1]


def test_fresh_tail_no_nodes_defers_when_no_user_in_tail():
    # Defer only when there is NOTHING to say: zero summary nodes AND no
    # real user row in the tail (transcript ends in synthetic-continuation
    # rows + tool spam, so the backward scan never finds a user anchor).
    msgs = []
    for _ in range(10):
        msgs.append({"role": "system", "content": "context"})
        msgs.append({"role": "assistant", "content": "narration"})
    msgs += [{"role": "tool", "tool_call_id": f"t{i}", "content": "spam"}
             for i in range(6)]
    e = _mk([], [])
    assert e._reduce_transcript("s1", msgs) is None


def test_tool_group_not_orphaned():
    msgs = _long(8)
    asst = {"role": "assistant", "content": "", "tool_calls": [
        {"id": "tX", "function": {"name": "terminal", "arguments": "{}"}}]}
    msgs += [asst, {"role": "tool", "tool_call_id": "tX", "content": "out"}]
    nodes = [(5, "- tool: ran", json.dumps([1, 2]))]
    e = _mk(nodes, [1, 2])
    out = e._reduce_transcript("s1", msgs)
    assert out is not None
    ids = {m.get("tool_call_id") for m in out if m.get("role") == "tool"}
    assert "tX" in ids  # kept in tail...
    i_tool = next(i for i, m in enumerate(out)
                  if m.get("tool_call_id") == "tX")
    assert any(
        out[j].get("tool_calls") and any(
            (tc.get("id") or tc.get("call_id")) == "tX"
            for tc in out[j]["tool_calls"] if isinstance(tc, dict))
        for j in range(i_tool))  # ...with its assistant call kept too


def test_covered_nodes_deduped():
    # node 9 covers the same messages as newer node 10 -> dropped once
    nodes = [(9, "old", json.dumps([1, 2])), (10, "new", json.dumps([1, 2]))]
    e = _mk(nodes, [1, 2])
    out = e._reduce_transcript("s1", _long())
    body = next(m["content"] for m in out
                if str(m.get("content", "")).startswith(
                    engine.HANDOFF_PREFIX[:40]))
    assert "[node 10]" in body and "[node 9]" not in body


def test_awaiting_flag_only_on_reduction():
    e = _mk([], [])
    assert e.compress(_long(3)) is not None
    assert e.awaiting_real_usage_after_compression is False
    e2 = _mk([(3, "s", json.dumps([1]))], [1])
    out = e2.compress(_long())
    assert e2.awaiting_real_usage_after_compression is (
        len(out) < len(_long()))


if __name__ == "__main__":
    for fn in sorted(globals()):
        if fn.startswith("test_"):
            globals()[fn]()
            print("PASS", fn)
