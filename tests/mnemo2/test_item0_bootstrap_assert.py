"""Item-0 regression: fresh-host bootstrap must fail LOUD, never silent.

Class (manny census 2026-11-03 01:02): on trade/warren the :54331 service was
active and LISTENing yet every real login answered 'role does not exist' —
an initdb/bootstrap leg that silently no-oped looks healthy from
`systemctl is-active`, so every fresh-host roll proceeded and died
pure-sqlite. Same never-trust-the-surface family as the T-2 lesson.

Pins:
  1. scripts/bootstrap_verify.sh exists and fails CLOSED (non-zero) when the
     instance it points at does not exist (fresh-host bootstrap-no-op shape),
     and reports a BOOTSTRAP_FAIL line naming the leg.
  2. scripts/onboard_profile.sh set_param can no longer merge two KEY= lines
     when a value carries a trailing newline (the sed-swapload corruption
     that made the DSN URI unparseable -> fail-closed sqlite fallback).
  3. scripts/env_dsn_check.sh detects a merged DSN line and passes a clean
     one (deterministic, red/green).
"""
import os
import pathlib
import subprocess

REPO = pathlib.Path(__file__).resolve().parents[2]
SCRIPTS = REPO / "scripts"


def _run(script, *args, env=None):
    return subprocess.run(["bash", str(SCRIPTS / script), *args],
                          capture_output=True, text=True, timeout=60,
                          env=env)


def test_bootstrap_verify_fails_closed_on_missing_instance(tmp_path):
    """BASE pointing at a never-bootstrapped dir = fresh-host no-op shape:
    must exit non-zero and name the A1 leg, never print BOOTSTRAP_VERIFY OK."""
    env = dict(os.environ)
    env.update(BASE=str(tmp_path / "pg"), PORT="59999", ROLE="nosuchrole",
               PGBIN=str(pathlib.Path.home() / "lcm-pg" / "pg" / "bin"))
    if not (pathlib.Path(env["PGBIN"]) / "psql").exists():
        # no local postgres binaries at all: the script must STILL fail closed
        env["PGBIN"] = ""
    r = _run("bootstrap_verify.sh", "qa_agent", env=env)
    assert r.returncode != 0, "bootstrap_verify passed against a dead instance"
    assert "BOOTSTRAP_FAIL" in r.stderr or "BOOTSTRAP_VERIFY FAILED" in r.stdout
    assert "BOOTSTRAP_VERIFY OK" not in r.stdout


def test_onboard_set_param_newline_safe(tmp_path):
    """A value with a trailing newline must replace the KEY line as ONE
    line — the old sed swapped the newline in and swallowed the next
    (merged-DSN corruption)."""
    import re
    src = (SCRIPTS / "onboard_profile.sh").read_text()
    body = re.search(r"  set_param\(\) \{.*?\n  \}", src, re.S)
    assert body, "set_param leg not found in onboard_profile.sh"
    penv = tmp_path / ".env"
    penv.write_text("LCM2_PG_DSN=postgresql:///old?host=/a\nNEXT_PARAM=keepme\n")
    snippet = (
        f"PENV={penv}\n"
        + body.group(0).strip() + "\n"
        + "set_param LCM2_PG_DSN \"$(printf 'postgresql:///new?host=/b\\n')\"\n"
    )
    subprocess.run(["bash", "-c", snippet], check=True, timeout=30)
    lines = penv.read_text().splitlines()
    dsn = [l for l in lines if l.startswith("LCM2_PG_DSN=")]
    assert len(dsn) == 1 and "NEXT_PARAM" not in dsn[0], \
        f"merged/corrupted line survived: {lines}"
    assert any(l.startswith("NEXT_PARAM=") for l in lines), "next line swallowed"


def test_env_dsn_check_red_green(tmp_path):
    bad = tmp_path / "bad.env"
    bad.write_text("LCM2_PG_DSN=postgresql:///a?host=/xLCM2_PG_DSN=postgresql:///a?host=/y\n")
    r = _run("env_dsn_check.sh", str(bad))
    assert r.returncode != 0 and "merged DSN" in r.stderr
    good = tmp_path / "good.env"
    good.write_text("LCM2_PG_DSN=postgresql:///lcm2db?host=/x&port=54331&user=mnemo201\n")
    r = _run("env_dsn_check.sh", str(good))
    assert r.returncode == 0, r.stdout + r.stderr
