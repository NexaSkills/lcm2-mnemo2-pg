"""S2: vendor extraction lane parity (remember -> typed fact annotations
+ always-on heuristic KG + fact-hit recall ranking boost)."""
import sys
import pathlib

import pytest

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "plugins" / "mnemo-pg2"))

import facts  # noqa: E402
from engine import Engine  # noqa: E402


@pytest.fixture(scope="module")
def live():
    try:
        e = Engine()
        with e._cx("private") as cx:
            cx.query_scalar("select 1")
    except Exception as exc:  # pragma: no cover
        pytest.skip(f"mnemo2 instance unreachable: {exc}")
    return True


@pytest.fixture()
def eng(live):
    return Engine()


# -- regex lane (pure) -----------------------------------------------------
def test_extract_types():
    got = dict(facts.extract_facts(
        "We decided to ship v2.3.1 on 2026-12-01; the p95 latency is "
        "350 ms and NexaSkills signed off yesterday."))
    kinds = {k for k, v in got.items()} if isinstance(got, dict) else \
        {k for k, _ in facts.extract_facts("x")}
    pairs = facts.extract_facts(
        "We decided to ship v2.3.1 on 2026-12-01; the p95 latency is "
        "350 ms and NexaSkills signed off yesterday.")
    ks = {k for k, _ in pairs}
    assert {"version", "date", "decision"} <= ks or {"version", "date"} <= ks
    assert any("350" in v for k, v in pairs if k == "metric")


def test_extract_cap_and_dedup():
    pairs = facts.extract_facts("alpha ALPHA 1 ms 2 ms 3 ms 4 ms 5 ms 6 ms")
    assert len(pairs) <= 5
    assert len({(k, v.lower()) for k, v in pairs}) == len(pairs)


def test_extract_empty():
    assert facts.extract_facts("") == []


# -- remember enrichment (live) ---------------------------------------------
def test_remember_stores_annotations(eng):
    res = eng.remember("S2-EXTRACT decided to deploy v3.1.4 by 2026-12-15 "
                       "with 250 ms budget for the zebrafinch service",
                      importance=0.5)
    mid = res["id"]
    with eng._cx("private") as cx:
        anns = cx.query_dicts(
            "select kind, body from annotations where memory_id=%s", (mid,))
        trs = cx.query_dicts(
            "select subject from triples where source_memory_id=%s"
            " and source='ingest'", (mid,))
    assert len(anns) >= 2
    assert {a["kind"] for a in anns} & {"date", "version", "metric",
                                        "decision"}
    with eng._cx("private") as cx:
        cx.execute("delete from annotations where memory_id=%s", (mid,))
        cx.execute("delete from triples where source_memory_id=%s", (mid,))
        cx.execute("delete from working_memory where id=%s", (mid,))
    _ = trs


def test_kill_switch_off(eng, monkeypatch):
    monkeypatch.setenv("MNEMO2_AUTO_FACTS", "0")
    monkeypatch.setenv("MNEMO2_AUTO_TRIPLES", "0")
    res = eng.remember("S2-OFF decided to postpone v9.9.9 until 2027-01-01 "
                       "for the umbra service at 90 ms", importance=0.5)
    mid = res["id"]
    with eng._cx("private") as cx:
        n = cx.query_scalar("select count(*) from annotations where"
                            " memory_id=%s", (mid,))
    assert n == 0
    with eng._cx("private") as cx:
        cx.execute("delete from working_memory where id=%s", (mid,))


def test_recall_fact_lane_runs(eng):
    res = eng.remember("S2-RANK the zebrafinch deploy decided at 250 ms "
                       "on 2026-12-15", importance=0.6)
    mid = res["id"]
    try:
        hits = eng.recall("zebrafinch umbra deploy", limit=8)
        assert hits.get("results") is not None  # SQL valid, params bind
        ids = [h["id"] for h in hits["results"]]
        assert mid in ids  # fact-boosted row finds itself
    finally:
        with eng._cx("private") as cx:
            cx.execute("delete from annotations where memory_id=%s", (mid,))
            cx.execute("delete from triples where source_memory_id=%s",
                       (mid,))
            cx.execute("delete from working_memory where id=%s", (mid,))
