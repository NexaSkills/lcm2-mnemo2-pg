"""P4-A: recall reinforcement (vendor beam.py:7349 parity).

Live-instance test (conftest contract): remembering then recalling a row
twice must bump recall_count 0 -> 2 and stamp last_recalled. The env kill
switch must freeze the counters. Lossless invariant: the bump never
mutates content/created_at.
"""
import os
import pathlib
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "plugins" / "mnemo-pg2"))

import pgconn  # noqa: E402
from engine import Engine  # noqa: E402


@pytest.fixture(scope="module")
def live():
    try:
        with pgconn.connect(None, None, ddl=True) as cx:
            cx.query_scalar("select 1")
    except Exception as exc:  # pragma: no cover - env guard
        pytest.skip(f"mnemo2 instance unreachable: {exc}")
    return True


@pytest.fixture()
def probe(live):
    eng = Engine()
    content = "P4A-REINFORCE-PROBE-7731 reinforcement lane probe"
    out = eng.remember(content, importance=0.6, scope="global")
    mid = out.get("id")
    assert mid, out
    yield eng, mid, content
    try:
        eng.forget(mid)
    except Exception:
        pass


def _count(eng, mid):
    with eng._cx("private") as cx:
        return cx.query_dicts(
            "select recall_count, last_recalled is not null as stamped"
            " from working_memory where id = %s", (mid,))


def test_recall_bumps_counters(probe):
    eng, mid, q = probe
    assert eng.recall(q, limit=3)["ok"]
    rows = _count(eng, mid)
    assert rows and rows[0]["recall_count"] == 1, rows
    assert rows[0]["stamped"] is True
    eng.recall(q, limit=3)
    rows = _count(eng, mid)
    assert rows[0]["recall_count"] == 2, rows


def test_kill_switch_freezes_counters(probe, monkeypatch):
    eng, mid, q = probe
    monkeypatch.setenv("MNEMO2_RECALL_REINFORCE", "0")
    eng.recall(q, limit=3)
    eng.recall(q, limit=3)
    rows = _count(eng, mid)
    assert rows[0]["recall_count"] == 0, rows
    assert rows[0]["stamped"] is False


def test_bump_preserves_row_content(probe):
    eng, mid, q = probe
    with eng._cx("private") as cx:
        before = cx.query_dicts(
            "select content, created_at from working_memory where id=%s",
            (mid,))
    eng.recall(q, limit=3)
    with eng._cx("private") as cx:
        after = cx.query_dicts(
            "select content, created_at from working_memory where id=%s",
            (mid,))
    assert before == after
