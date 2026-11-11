"""Bank-trace diagnostic (nexa 2026-11-11): profile-scoped CLI child
sessions silently inherited the spawning shell's HERMES_HOME and their
remember rows landed in that profile's bank (mnemo2_devon, not
mnemo2_default) with nothing in any log explaining the routing. The fix
is observability, not behaviour change: agent_schema() logs ONE
informational line naming the derivation (env-pin | host-funnel |
HERMES_HOME | default-fallback) so the operator can see WHY a bank was
chosen. Behaviour guard: the schema itself keeps deriving exactly as
before (deterministic mnemo2_<profile> from the live home).
"""
import logging
import pathlib
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "plugins" / "mnemo-pg2"))

import pgconn  # noqa: E402


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    pgconn._bank_log_reset()
    monkeypatch.delenv("MNEMO2_SCHEMA", raising=False)
    monkeypatch.delenv("HERMES_HOME", raising=False)
    # hermes_constants absent in the test interpreter: the funnel probe
    # in home_source() fails open to the env/default attribution
    yield
    pgconn._bank_log_reset()


def test_env_inheritance_is_named(tmp_path, monkeypatch, caplog):
    # exactly the incident shape: CLI child with HERMES_HOME exported
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "profiles" / "bob"))
    with caplog.at_level(logging.INFO, logger="mnemo-pg2"):
        s = pgconn.agent_schema()      # the log line rides the FIRST resolution
    assert s == "mnemo2_bob"           # behaviour UNCHANGED
    lines = [r for r in caplog.records if "private bank=" in r.getMessage()]
    assert len(lines) == 1             # once per process
    assert "via HERMES_HOME" in lines[0].getMessage()


def test_pin_named(tmp_path, monkeypatch, caplog):
    monkeypatch.setenv("MNEMO2_SCHEMA", "mnemo2_pinned")
    with caplog.at_level(logging.INFO, logger="mnemo-pg2"):
        s = pgconn.agent_schema()
    assert s == "mnemo2_pinned"
    lines = [r for r in caplog.records if "private bank=" in r.getMessage()]
    assert lines and "via env-pin" in lines[0].getMessage()


def test_once_per_process(tmp_path, monkeypatch, caplog):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "profiles" / "bob"))
    with caplog.at_level(logging.INFO, logger="mnemo-pg2"):
        pgconn.agent_schema()
        pgconn.agent_schema()          # second resolution logs nothing
    lines = [r for r in caplog.records if "private bank=" in r.getMessage()]
    assert len(lines) == 1


def test_default_fallback_named(caplog):
    with caplog.at_level(logging.INFO, logger="mnemo-pg2"):
        pgconn.agent_schema()
    lines = [r for r in caplog.records if "private bank=" in r.getMessage()]
    assert lines and "via default-fallback" in lines[0].getMessage()
