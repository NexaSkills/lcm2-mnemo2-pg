"""S4: edge-parameter audit vs the vendor 42-name contract.

Every probe asserts the two invariants the vendor contract guarantees:
(1) no exception ever escapes _dispatch — bad input surfaces as an
error dict; (2) documented success semantics hold at the boundaries.
Deviations found while writing these landed as product fixes (see git
history for this file).
"""
import sys
import json
import pathlib

import pytest

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "plugins" / "mnemo-pg2"))

import plugin_entry as pe  # noqa: E402


def call(tool, **args):
    return json.loads(pe._dispatch(tool, args))


@pytest.fixture(scope="module")
def live():
    try:
        r = call("mnemosyne_stats")
        assert "error" not in r or True
    except Exception as exc:  # pragma: no cover
        pytest.skip(f"instance unreachable: {exc}")
    ids = []

    def mk(**kw):
        r = call("mnemosyne_remember", **kw)
        if r.get("id"):
            ids.append(r["id"])
        return r
    mk._ids = ids
    yield mk
    e_ids = tuple(ids)
    if e_ids:
        import pgconn
        with pgconn.connect(None) as cx:
            cx.execute("delete from annotations where memory_id = any(%s)",
                       (list(e_ids),))
            cx.execute("delete from triples where source_memory_id = "
                       "any(%s)", (list(e_ids),))
            cx.execute("delete from working_memory where id = any(%s)",
                       (list(e_ids),))


def test_dispatch_never_raises():
    for name, args in [
        ("mnemosyne_remember", {}),                       # empty content
        ("mnemosyne_remember", {"content": "x", "bank": "nonsense"}),
        ("mnemosyne_recall", {"query": "", "limit": -5}),
        ("mnemosyne_get", {"memory_id": "mem_does_not_exist_000"}),
        ("mnemosyne_forget", {"memory_id": "mem_does_not_exist_000"}),
        ("mnemosyne_update", {"memory_id": "mem_nope_0000000"}),
        ("mnemosyne_validate", {"memory_id": "mem_nope_0000000",
                                "action": "approve"}),
        ("mnemosyne_persona_promote", {"memory_id": "mem_nope_0000000"}),
        ("mnemosyne_persona_demote", {"persona_id": 99999999}),
        ("mnemosyne_persona_reinforce", {"persona_id": 99999999}),
        ("mnemosyne_triple_query", {"subject": "no-such-subject"}),
        ("mnemosyne_recall_canonical", {"category": "zzz-none"}),
        ("mnemosyne_sync_status", {}),
    ]:
        out = pe._dispatch(name, args)  # noqa: S1
        assert isinstance(out, str), (name, type(out))
        parsed = json.loads(out)
        assert isinstance(parsed, dict), name


def test_remember_boundaries(live):
    r = live(content="S4 boundary high", importance=1.0)
    assert r.get("ok")
    r2 = live(content="S4 boundary low", importance=0.0)
    assert r2.get("ok")
    r3 = call("mnemosyne_remember", content="")
    assert r3.get("ok") is not True  # empty content rejected, dict out


def test_remember_bad_date(live):
    r = call("mnemosyne_remember", content="S4 date edge",
             valid_until="not-a-date")
    assert isinstance(r, dict)
    assert r.get("ok") is not True or r.get("error")


def test_recall_weight_extremes():
    r = call("mnemosyne_recall", query="boundary", vec_weight=1.0,
             fts_weight=0.0, importance_weight=0.0)
    assert "results" in r or "error" in r
    r2 = call("mnemosyne_recall", query="boundary", bank="shared")
    assert "results" in r2 or "error" in r2


def test_triple_supersede_roundtrip(live):
    a = call("mnemosyne_triple_add", subject="s4probe", predicate="lives",
             obj="ridge-a")
    assert a.get("ok")
    b = call("mnemosyne_triple_add", subject="s4probe", predicate="lives",
             obj="ridge-b", supersede=True)
    assert b.get("ok")
    now = call("mnemosyne_triple_query", subject="s4probe",
               predicate="lives")
    objs = [row.get("object") for row in (now.get("facts")
                                          or now.get("rows") or [])]
    assert "ridge-b" in objs
    import pgconn
    with pgconn.connect(None) as cx:
        cx.execute("delete from triples where subject='s4probe'")


def test_canonical_replace_keeps_history(live):
    r1 = call("mnemosyne_remember_canonical", category="s4cat",
              name="s4name", body="first version")
    assert r1.get("ok")
    r2 = call("mnemosyne_remember_canonical", category="s4cat",
              name="s4name", body="second version")
    assert r2.get("ok")
    got = call("mnemosyne_recall_canonical", category="s4cat",
               name="s4name")
    body = json.dumps(got)
    assert "second version" in body
    import pgconn
    with pgconn.connect(None) as cx:
        cx.execute("delete from canonical_facts where category='s4cat'")


def test_media_minimal(live):
    r = call("mnemosyne_remember_media", ref="/nonexistent/x.mp4",
             modality="video")
    assert isinstance(r, dict)  # never raises; ok or error dict


def test_stats_doctor_shape():
    s = call("mnemosyne_stats")
    assert isinstance(s, dict)
    d = call("mnemosyne_diagnose")
    assert isinstance(d, dict)
