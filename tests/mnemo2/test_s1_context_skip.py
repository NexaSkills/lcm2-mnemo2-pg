"""S1: write_policy context-skip (vendor _skip_contexts parity)."""
import sys
import pathlib

import pytest

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "plugins" / "mnemo-pg2"))

import capture  # noqa: E402
import memory_provider as mp  # noqa: E402
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


def test_context_ok_table():
    assert capture.context_ok("local-abc") is True
    assert capture.context_ok("local-x", platform="cron") is False
    assert capture.context_ok("local-x", platform="subagent") is False
    assert capture.context_ok("local-x", platform="CLI") is True
    assert capture.context_ok("local-x", side_agent=True) is False
    assert capture.context_ok("local-x", parent_session_id="p") is False
    assert capture.context_ok("20261105_010203_abcd") is False  # cron shape
    assert capture.context_ok("2026-11-05-abc") is True  # not the shape


def test_skip_env_override(monkeypatch):
    monkeypatch.setenv("MNEMO2_SKIP_CONTEXTS", "flush")
    assert capture.context_ok("s", platform="cron") is True
    assert capture.context_ok("s", platform="flush") is False
    monkeypatch.setenv("MNEMO2_SKIP_CRON_SESSIONS", "0")
    assert capture.context_ok("20261105_010203_abcd") is True


def test_cron_session_not_captured(live, monkeypatch):
    monkeypatch.setenv("MNEMO2_AUTO_CAPTURE", "1")
    prov = mp._make_provider_module().provider
    prov.sync_turn("S1-CRON should never be stored anywhere",
                   "cron reply body stored nowhere",
                   session_id="20261201_101112_dead")
    e = Engine()
    with e._cx("private") as cx:
        n = cx.query_scalar("select count(*) from working_memory where"
                            " session_id='20261201_101112_dead'")
        m = cx.query_scalar("select count(*) from turn_ledger where"
                            " session_id='20261201_101112_dead'")
    assert n == 0 and m == 0


def test_side_agent_context_not_captured(live, monkeypatch):
    monkeypatch.setenv("MNEMO2_AUTO_CAPTURE", "1")
    prov = mp._make_provider_module().provider
    prov.initialize("s1-side", platform="cli", side_agent=True)
    prov.sync_turn("S1-SIDE unique child agent content here",
                   "child reply content body", session_id="s1-side")
    e = Engine()
    with e._cx("private") as cx:
        n = cx.query_scalar("select count(*) from working_memory where"
                            " session_id='s1-side'")
    assert n == 0
    # reset context attrs so later tests in this process stay primary
    prov.initialize("s1-side", platform="cli")


def test_sleep_single_flight_lock_ok(live):
    # lock acquire/release round-trip works on the product path
    e = Engine()
    with e._cx("private") as cx:
        cx.execute("SELECT pg_advisory_lock(hashtext('mnemo2_llm_slot'))")
        cx.execute("SELECT pg_advisory_unlock(hashtext('mnemo2_llm_slot'))")
