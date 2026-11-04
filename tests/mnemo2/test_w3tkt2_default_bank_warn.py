"""W3TKT2-MIKE-1235 #5 — repo-side runtime guard: agent_schema() must WARN
(exactly once per process — the default-bank-derivation latch) when it
derives `mnemo2_<p>` from a scoped home whose profile is NOT served by the
host (no identity marker), because the vendor fall-through then re-roots
the turn to the LAUNCH home and every subsequent write banks to
mnemo2_default (the ghost profile now silently owns the default bank).

Mechanism (vendor Hermes code — NEVER edited from this repo; cited in the
CLOSE row): gateway/run.py _resolve_profile_home_for_source scopes the
launch home when hermes profile_exists() is False (config.yaml AND every
hermes_constants._PROFILE_IDENTITY_MARKERS entry absent), so the scoped
home seen at first derivation disappears for the rest of the turn.

Two levers, both repo-side, no vendor edit:
  1. scripts/mnemo2_profile_dsn_audit.sh (commit 1 of #5) — the operator
     fail-closed: every SERVED profile must carry MNEMO2_PG_DSN; wired
     into cutover_profile.sh §3 (cutover fails RED on an offender).
  2. THIS guard — the runtime signal: an onboarded-but-ghost dir (or a
     ghost a rival created) fires ONE named warning per derivation
     instead of banking silently forever.

Test surface (deterministic, no pinned profile list, fleet practice):
pgconn._profile_served() (host predicate first — hermes_constants' OWN
function when importable, else the local mirror over
_PROFILE_IDENTITY_MARKERS_LOCAL), pgconn._default_bank_warning_state() and
the _default_bank_warning_reset() test hook.
"""
import logging
import pathlib
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "plugins" / "mnemo-pg2"))

import pgconn  # noqa: E402


@pytest.fixture()
def latch():
    state = getattr(pgconn, "_default_bank_warning_state", None)
    reset = getattr(pgconn, "_default_bank_warning_reset", None)
    assert state is not None and reset is not None, \
        "pgconn must expose _default_bank_warning_state/_reset hooks"
    reset()
    yield state
    reset()


def _warns(caplog):
    return [r.getMessage() for r in caplog.records
            if "mnemo2_default" in r.getMessage()]


def test_ghost_profile_derivation_warns_once(monkeypatch, caplog, latch):
    """Scoped home = profiles/<p> whose dir has NO identity marker => the
    fall-through class: schema still derives (writes unchanged) AND exactly
    ONE named warning fires; further derivations stay silent (latched)."""
    monkeypatch.delenv("MNEMO2_SCHEMA", raising=False)
    ghost = caplog  # unused placeholder to keep signature simple
    base = pathlib.Path(pgconn._live_home()).resolve().parent  # <base>/profiles/...
    ghost_home = str(base / "profiles" / "w3tkt2ghost")
    monkeypatch.setattr(pgconn, "_live_home", lambda: ghost_home)
    # ghost dir physically absent/empty on purpose (never served).
    import logging as _lg
    with caplog.at_level(_lg.WARNING, logger="mnemo-pg2"):
        s1 = pgconn.agent_schema()
        s2 = pgconn.agent_schema()
    assert s1 == "mnemo2_w3tkt2ghost" and s2 == "mnemo2_w3tkt2ghost"
    w = _warns(caplog)
    assert len(w) == 1, f"expected exactly one latched warning, got {w}"
    assert "w3tkt2ghost" in w[0] and "mnemo2_default" in w[0]


def test_served_profile_never_warns(tmp_path, monkeypatch, caplog, latch):
    """A profile dir the host SERVES (config.yaml marker — host predicate)
    => zero warnings, schema derives as always. No false positives on a
    healthy fleet host."""
    monkeypatch.delenv("MNEMO2_SCHEMA", raising=False)
    pdir = tmp_path / "profiles" / "servedp"
    pdir.mkdir(parents=True)
    (pdir / "config.yaml").write_text("x: 1\n")
    monkeypatch.setattr(pgconn, "_live_home", lambda: str(pdir))
    import logging as _lg
    with caplog.at_level(_lg.WARNING, logger="mnemo-pg2"):
        s = pgconn.agent_schema()
    assert s == "mnemo2_servedp"
    assert _warns(caplog) == []


def test_default_home_derivation_never_warns(monkeypatch, caplog, latch):
    """Home = the launch home itself (basename .hermes) => mnemo2_default
    by design, silent (that is the plain default profile, not the class)."""
    monkeypatch.delenv("MNEMO2_SCHEMA", raising=False)
    monkeypatch.setattr(pgconn, "_live_home",
                        lambda: str(pathlib.Path.home() / ".hermes"))
    import logging as _lg
    with caplog.at_level(_lg.WARNING, logger="mnemo-pg2"):
        s = pgconn.agent_schema()
    assert s == "mnemo2_default"
    assert _warns(caplog) == []


def test_explicit_schema_pin_never_warns(monkeypatch, caplog, latch):
    """MNEMO2_SCHEMA explicit pin wins BEFORE any derivation: the
    fall-through class cannot apply, so the guard must stay silent."""
    ghost_home = str(pathlib.Path(pgconn._live_home()).resolve().parent
                     / "profiles" / "w3tkt2ghost")
    monkeypatch.setattr(pgconn, "_live_home", lambda: ghost_home)
    monkeypatch.setenv("MNEMO2_SCHEMA", "mnemo2_pinned")
    import logging as _lg
    with caplog.at_level(_lg.WARNING, logger="mnemo-pg2"):
        s = pgconn.agent_schema()
    assert s == "mnemo2_pinned"
    assert _warns(caplog) == []


def test_host_predicate_used_when_importable(tmp_path, monkeypatch, caplog, latch):
    """Fleet practice: NEVER a pinned rule when the host exposes one — the
    guard must consult the host's OWN identity predicate
    (hermes_constants.named_profile_is_live — the exact check behind
    hermes_cli.profiles.profile_exists that vendor run.py's fall-through
    calls). Stub proves the host route is taken; a served answer stays
    silent."""
    import types
    calls = []

    hc = types.ModuleType("hermes_constants")
    setattr(hc, "named_profile_is_live",
            lambda home, **kw: (calls.append(home), True)[1])
    monkeypatch.setitem(sys.modules, "hermes_constants", hc)
    monkeypatch.delenv("MNEMO2_SCHEMA", raising=False)
    ghost_home = str(pathlib.Path(pgconn._live_home()).resolve().parent
                     / "profiles" / "w3tkt2ghost2")
    monkeypatch.setattr(pgconn, "_live_home", lambda: ghost_home)
    import logging as _lg
    with caplog.at_level(_lg.WARNING, logger="mnemo-pg2"):
        s = pgconn.agent_schema()
    assert s == "mnemo2_w3tkt2ghost2"
    assert calls, "host predicate must be consulted when importable"
    assert pathlib.Path(calls[0]) == pathlib.Path(ghost_home), \
        "host predicate must be probed on the path IN HAND, never re-resolved"
    assert _warns(caplog) == []

    # NOT-served answer from the host predicate => warning fires (route is
    # authoritative: the local mirror must never contradict the host).
    calls.clear()
    hc2 = types.ModuleType("hermes_constants")
    setattr(hc2, "named_profile_is_live",
            lambda home, **kw: (calls.append(home), False)[1])
    monkeypatch.setitem(sys.modules, "hermes_constants", hc2)
    # marker physically present but host says NOT served => still warns
    pathlib.Path(ghost_home).mkdir(parents=True, exist_ok=True)
    (pathlib.Path(ghost_home) / "config.yaml").write_text("a: 1\n")
    pgconn._default_bank_warning_reset()
    pgconn._default_bank_warning_reset()
    with caplog.at_level(_lg.WARNING, logger="mnemo-pg2"):
        s2 = pgconn.agent_schema()
    assert s2 == "mnemo2_w3tkt2ghost2"
    assert _warns(caplog), "host NOT-served answer must fire the warning"
    # cleanup the scratch ghost we created (never a real host dir)
    import shutil
    shutil.rmtree(ghost_home, ignore_errors=True)
