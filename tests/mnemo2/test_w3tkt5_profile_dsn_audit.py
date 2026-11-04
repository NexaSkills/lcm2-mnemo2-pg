"""W3TKT-MIKE-1220 #5 — the routed-turn bank-resolution lever, root-caused
to the host layer, guarded repo-side (CLOSE row carries the named BLOCKED
mechanism + live evidence).

Mechanism (all cited live on beez 2026-11-04, host code paths verified by
reading them, not assumed):
  * A multiplexed gateway serves the profiles hermes_cli.profiles
    .profiles_to_serve lists — a named dir counts ONLY when hermes
    .named_profile_is_live(dir): exists AND carries an identity marker
    (config.yaml / .env / SOUL.md / profile.yaml / auth.json / state.db
    — hermes_constants._PROFILE_IDENTITY_MARKERS) AND not tombstoned.
  * For a routed source whose profile fails profile_exists(), gateway/
    run.py::_resolve_profile_home_for_source logs 'does not exist for
    source ... falling back to global HERMES_HOME' and returns the LAUNCH
    home; _profile_runtime_scope then sets the contextvar home to that —
    pgconn._live_home (the B-2 funnel) faithfully reports it, so
    agent_schema() derives mnemo2_default. The funnel is NOT the lever:
    it already follows whatever home the host scopes (see
    test_b2_leak_contextvar_home.py). The lever is host profile identity
    state — the exact situation of a profiles/<p>/ whose config.yaml is
    ABSENT with no other marker (tessa C6, template 12:03).
  * beez-live proof of the predicate: marker-less fixture dir =>
    profile_exists=False / named_profile_is_live=False; the same dir with
    one marker => True/True (probe cited in the CLOSE row; /p/<ghost>/
    dispatch 404s 'Unknown or unconfigured profile' — an unserved ghost
    is never even routed).

Repo-side fail-closed (this test): every SERVED profile MUST carry its
own MNEMO2_PG_DSN (scripts/mnemo2_profile_dsn_audit.sh). With that line
present, the B-2 funnel plus the register-time bind layer (P3L) resolve
the profile-true DSN inside the routed turn; without it, even a correct
home leaves writes on the composed default. The audit ships wired into
cutover_profile.sh's fail-closed assert block, and this file proves the
audit's semantics — including that a marker-less ghost dir is reported
UNSERVED (host-side action item, the BLOCKED row) instead of passing
silently.
"""
import pathlib
import subprocess

REPO = pathlib.Path(__file__).resolve().parents[2]
AUDIT = REPO / "scripts" / "mnemo2_profile_dsn_audit.sh"


def _run_audit(hermes_base):
    return subprocess.run(
        ["bash", str(AUDIT), "--base", str(hermes_base)],
        capture_output=True, text=True, timeout=60)


def _mkbase(base):
    base.mkdir(parents=True, exist_ok=True)
    (base / "config.yaml").write_text("")  # root-home marker
    return base


def _mkprofile(base, name, *, markers=(), env=None):
    d = base / "profiles" / name
    d.mkdir(parents=True, exist_ok=True)
    for m in markers:
        (d / m).write_text("")
    if env is not None:
        (d / ".env").write_text(env)
    return d


def test_served_profile_without_profile_dsn_fails_audit(tmp_path):
    """THE fail-closed assert: a served (identity-marked) profile whose
    .env lacks MNEMO2_PG_DSN must be named and fail the audit — that is
    the profile-true DSN the routed turn banks against; missing, writes
    sit on the composed default with no per-profile guard."""
    base = _mkbase(tmp_path / "hemes")
    _mkprofile(base, "goodp", markers=("config.yaml",),
               env="MNEMO2_PG_DSN=postgresql:///mnemo2db?host=x&port=1&user=u\n")
    _mkprofile(base, "badp", markers=("config.yaml", "SOUL.md"), env="")
    r = _run_audit(base)
    out = r.stdout + r.stderr
    assert "badp" in out, f"audit must name the DSN-less served profile:\n{out}"
    assert r.returncode != 0, "audit must exit non-zero (fail-closed)"


def test_marker_less_ghost_reported_unserved_not_passed(tmp_path):
    """A marker-less dir (config.yaml ABSENT and no other marker) is
    NOT served by the host — the audit names it UNSERVED (host-side fix
    per the CLOSE BLOCKED row) rather than pretending it banks fine."""
    base = _mkbase(tmp_path / "hemes2")
    _mkprofile(base, "ghostp", markers=())
    _mkprofile(base, "okp", markers=("state.db",),
               env="MNEMO2_PG_DSN=dbname=mnemo2db\n")
    r = _run_audit(base)
    out = r.stdout + r.stderr
    assert "ghostp" in out and "unserved" in out.lower(), \
        f"ghost dir must be reported as unserved:\n{out}"
    assert r.returncode == 0, \
        f"an unserved ghost is not an operator DSN action item:\n{out}"


def test_live_profile_needs_no_config_yaml_host_parity(tmp_path):
    """Host parity: hermes treats ANY identity marker as live (a .env-
    only profile IS served) — the audit must enumerate and DSN-check it,
    never via a pinned profile list (fleet practice)."""
    base = _mkbase(tmp_path / "hemes3")
    _mkprofile(base, "envonlyp", markers=(".env",), env="FOO=bar\n")
    r = _run_audit(base)
    out = r.stdout + r.stderr
    assert "envonlyp" in out, \
        f"env-marker-only live profile must be enumerated:\n{out}"
    assert r.returncode != 0, "…and fail closed with no MNEMO2_PG_DSN"


def test_cutover_profile_wires_the_audit():
    """The guard must be part of the ONE-PROCEDURE cutover's fail-closed
    assert block (cutover_profile.sh), not a loose script nobody runs."""
    src = (REPO / "scripts" / "cutover_profile.sh").read_text()
    assert "mnemo2_profile_dsn_audit.sh" in src, \
        "cutover_profile.sh assert block must run the DSN audit"
