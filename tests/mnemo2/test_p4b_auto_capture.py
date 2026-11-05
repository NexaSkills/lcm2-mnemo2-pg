"""P4-B: auto-capture lane (vendor sync_turn parity).

Sanitizer is pure (table-driven); ledger + capture run against the LIVE
instance (conftest contract). Default OFF (MNEMO2_AUTO_CAPTURE unset) must
write nothing; enabled must store sanitized [USER]/[ASSISTANT] rows,
source='auto', session-scoped, and a replayed turn must not double-store.
"""
import sys
import pathlib

import pytest

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "plugins" / "mnemo-pg2"))

import capture  # noqa: E402
import memory_provider as mp  # noqa: E402
from engine import Engine  # noqa: E402


@pytest.fixture(scope="module")
def prov():
    return mp._make_provider_module().provider


@pytest.fixture()
def eng(live):
    return Engine()


@pytest.fixture(scope="module")
def live():
    try:
        e = Engine()
        with e._cx("private") as cx:
            cx.query_scalar("select 1")
    except Exception as exc:  # pragma: no cover - env guard
        pytest.skip(f"mnemo2 instance unreachable: {exc}")
    return True


# -- sanitizer (pure) ------------------------------------------------------
CASES = [
    # (input, expected)
    ("plain question about the project", "plain question about the project"),
    ("[IMPORTANT: You are running as a scheduled cron job xyz]\nreal ask",
     "real ask"),
    ("[IMPORTANT: You are running as a scheduled cron job xyz]", None),
    ('[Replying to: "Cronjob Response: #42"]\nwhat changed?',
     "what changed?"),
    ("[IMPORTANT: MCP servers have been reloaded]", None),
    ("[System note: The following is recalled memory context ...]\nhi there",
     "hi there"),
    ("", ""),
    ("   ", ""),
]


@pytest.mark.parametrize("src,want", CASES)
def test_sanitizer_table(src, want):
    assert capture.sanitize(src) == want


def test_interrupted_boundary_keeps_tail():
    src = ("[System note: Your previous turn was interrupted. "
           "The interrupted request was:]\nplease continue the report")
    assert capture.sanitize(src) == "please continue the report"


# -- ledger (live) ----------------------------------------------------------
def test_ledger_idempotent(eng):
    a = eng._ledger_new_turn("s1", "h1")
    assert a == {"ok": True, "is_new": True}
    assert eng._ledger_new_turn("s1", "h1")["is_new"] is False
    assert eng._ledger_new_turn("s1", "h2")["is_new"] is True
    assert eng._ledger_new_turn("s2", "h1")["is_new"] is True
    with eng._cx("private") as cx:
        cx.execute("DELETE FROM turn_ledger WHERE session_id LIKE 's%'")


# -- sync_turn (live, provider-shaped call) ---------------------------------
def _auto_rows(eng, sid):
    with eng._cx("private") as cx:
        return cx.query_dicts(
            "select content, source, scope from working_memory"
            " where session_id = %s and source = 'auto'", (sid,))


def test_capture_default_off(prov, eng, monkeypatch):
    monkeypatch.delenv("MNEMO2_AUTO_CAPTURE", raising=False)
    prov.sync_turn("P4B-OFF-unique question string", "P4B-OFF answer body",
                   session_id="p4b-off")
    assert _auto_rows(eng, "p4b-off") == []


def test_capture_on_and_dedup(prov, eng, monkeypatch):
    monkeypatch.setenv("MNEMO2_AUTO_CAPTURE", "1")
    u = "P4B-ON what does the ledger say about the kite"
    a = "P4B-ON the ledger says the kite is in the copper drawer"
    prov.sync_turn(u, a, session_id="p4b-on")
    rows = _auto_rows(eng, "p4b-on")
    contents = {r["content"] for r in rows}
    assert f"[USER] {u}" in contents
    assert f"[ASSISTANT] {a}" in contents
    assert all(r["scope"] == "session" for r in rows)
    prov.sync_turn(u, a, session_id="p4b-on")   # replay: no doubles
    assert len(_auto_rows(eng, "p4b-on")) == 2
    with eng._cx("private") as cx:
        cx.execute("DELETE FROM working_memory WHERE session_id='p4b-on'")
        cx.execute("DELETE FROM turn_ledger WHERE session_id='p4b-on'")


def test_capture_pure_notice_stores_no_user_row(prov, eng, monkeypatch):
    monkeypatch.setenv("MNEMO2_AUTO_CAPTURE", "1")
    prov.sync_turn("[IMPORTANT: 2 background processes completed"
                   " successfully]", "noticing done here now",
                   session_id="p4b-nt")
    rows = _auto_rows(eng, "p4b-nt")
    assert not any(r["content"].startswith("[USER]") for r in rows)
    with eng._cx("private") as cx:
        cx.execute("DELETE FROM working_memory WHERE session_id='p4b-nt'")
        cx.execute("DELETE FROM turn_ledger WHERE session_id='p4b-nt'")
