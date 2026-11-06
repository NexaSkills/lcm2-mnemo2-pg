"""S3: vendor _maybe_auto_sleep parity (turn-opportunistic, no timers)."""
import sys
import time
import pathlib

import pytest

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "plugins" / "mnemo-pg2"))

import memory_provider as mp  # noqa: E402
from engine import Engine  # noqa: E402

SESS = "s3-autosleep"


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
def seeded(live):
    e = Engine()
    ids = [f"mem_s3seed{i}" for i in range(60)]
    with e._cx("private") as cx:
        cx.execute("delete from working_memory where session_id=%s", (SESS,))
        for mid in ids:
            cx.execute(
                "insert into working_memory(id,content,source,importance,"
                "session_id) values (%s,%s,'user',0.3,%s)",
                (mid, f"S3 seed row {mid}: the quota resets at dawn", SESS))
        cx.execute("update working_memory set created_at = created_at -"
                   " interval '48 hours' where session_id=%s", (SESS,))
        cx.execute("delete from meta where key='budget:auto_sleep'")
    yield ids
    with e._cx("private") as cx:
        cx.execute("delete from meta where key='budget:auto_sleep'")
    with e._cx("private") as cx:
        cx.execute("delete from annotations where memory_id in"
                   " (select id from working_memory where session_id=%s)",
                   (SESS,))
        cx.execute("delete from triples where source_memory_id in"
                   " (select id from working_memory where session_id=%s)",
                   (SESS,))
        cx.execute("delete from episodic_memory where session_id=%s", (SESS,))
        cx.execute("delete from memory_embeddings where memory_id in"
                   " (select id from episodic_memory where session_id=%s)",
                   (SESS,))
        cx.execute("delete from working_memory where session_id=%s", (SESS,))


def _marked():
    e = Engine()
    with e._cx("private") as cx:
        return cx.query_scalar(
            "select count(*) from working_memory where consolidated_at"
            " is not null and session_id=%s", (SESS,))


def test_reserve_budget_atomic(live):
    e = Engine()
    with e._cx("private") as cx:
        cx.execute("delete from meta where key='budget:unitkey'")
    assert e.reserve_budget(key="unitkey", secs=1800)["ok"] is True
    assert e.reserve_budget(key="unitkey", secs=1800)["ok"] is False
    assert e.reserve_budget(key="unitkey", secs=0)["ok"] is True
    with e._cx("private") as cx:
        cx.execute("delete from meta where key='budget:unitkey'")


def test_auto_sleep_fires_on_turn(live, seeded, monkeypatch):
    monkeypatch.setenv("MNEMO2_AUTO_CAPTURE", "1")
    monkeypatch.setenv("MNEMO2_AUTO_SLEEP", "1")
    monkeypatch.setenv("MNEMO2_AUTO_SLEEP_THRESHOLD", "5")
    # deterministic: force sleep's concat path (a real proxy key in the
    # environment would make the slow main model race the poll window)
    monkeypatch.delenv("MNEMO2_LLM_KEY", raising=False)
    monkeypatch.delenv("HERMES_CUSTOM_AI_NEXASKILLS_CO_ZA_API_KEY",
                       raising=False)
    prov = mp._make_provider_module().provider  # fresh gate
    prov.sync_turn("S3 trigger message with unique plumbus content",
                   "S3 reply body that is reasonably long",
                   session_id="s3-trigger")
    deadline = time.time() + 20
    while time.time() < deadline:
        if _marked() > 0:
            break
        time.sleep(0.5)
    assert _marked() > 0


def test_auto_sleep_kill_switch(live, seeded, monkeypatch):
    monkeypatch.setenv("MNEMO2_AUTO_CAPTURE", "1")
    monkeypatch.setenv("MNEMO2_AUTO_SLEEP", "0")
    prov = mp._make_provider_module().provider
    before = _marked()
    prov.sync_turn("S3 trigger two with unique qux content here",
                   "S3 reply body number two reasonably long",
                   session_id="s3-trigger-2")
    time.sleep(2)
    assert _marked() == before


def test_below_threshold_stays_quiet(live, monkeypatch):
    monkeypatch.setenv("MNEMO2_AUTO_CAPTURE", "1")
    monkeypatch.setenv("MNEMO2_AUTO_SLEEP", "1")
    monkeypatch.setenv("MNEMO2_AUTO_SLEEP_THRESHOLD", "100000")
    prov = mp._make_provider_module().provider
    before = _marked()
    prov.sync_turn("S3 trigger three with unique wazz content",
                   "S3 reply three body with adequate length",
                   session_id="s3-trigger-3")
    time.sleep(2)
    assert _marked() == before
