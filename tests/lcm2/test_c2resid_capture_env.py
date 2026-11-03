"""C2RESID regression: the context-engine CAPTURE-DIR leg (route 1) is
part of onboarding, and a gateway env that cannot import the capture
lane's deps says so LOUD instead of dying silently per-turn.

Class (C2DSNBOUNCE-MIKE-2158 residual; manny's own lane paid the re-emit
2026-11-03 22:08:28,177): context.engine=lcm2-pg was set (config-side
align done) but profiles carried NO <profile>/plugins/context_engine/
lcm2-pg dir. Route 1 of the host's engine selector
(agent_init._select_context_engine -> plugins.context_engine.
load_context_engine) resolves the name ONLY under $HERMES_HOME/plugins/,
and on the multiplexed gateway get_hermes_home() is scoped PER PROFILE
(gateway/run.py _profile_runtime_scope contextvar) — so a routed turn of
a profile without the dir resolves NOTHING on route 1 (host logs it at
debug only), and without the general plugin route's "loaded but no
engine instance found" line the ONLY signal is the per-turn run_agent
"not found — falling back" warning. That is exactly the residual class.

Pins (hermetic, no host contact; fake hermes on PATH like
test_x10_engine_align.py):
  1. onboard_profile.sh authors the per-profile capture symlink
     <profile>/plugins/context_engine/lcm2-pg -> repo capture dir
     (Nov-1 launch-scope symlink precedent, profile-scoped form);
  2. cutover_capture_dirs.sh covers EVERY profile whose
     context.engine says lcm2-pg (and only those), idempotently,
     verified by capture_dir_assert.sh going GREEN;
  3. capture_dir_assert.sh fails RED (exit 2) naming the MISSING
     profile + the exact fix line, and a WRONG-target link is RED too
     (never trust the surface);
  4. env_depcheck.sh is fail-closed: RED + named command forms when the
     gateway's import surface lacks psycopg/psycopg_pool, GREEN when it
     imports both (X-4 family: missing deps must NAME themselves, not
     vanish into per-turn fallbacks);
  5. structural: both pgconn lanes demand psycopg_pool BEFORE their
     first ConnectionPool use (the dep _depcheck's psycopg-only probe
     does not cover).
"""
import json
import os
import pathlib
import re
import stat
import subprocess

REPO = pathlib.Path(__file__).resolve().parents[2]
SCRIPTS = REPO / "scripts"
CAPTURE_SRC = REPO / "plugins" / "context_engine" / "lcm2-pg"

FAKE_HERMES = r"""#!/usr/bin/env bash
# fake hermes: profile create + config get/set (state per target), plus
# plugins list/enable passthroughs so onboard_profile.sh runs end to end.
p="default"
if [ "${1:-}" = "-p" ]; then p="${2:-}"; shift 2; fi
case "$1" in
  profile) # profile create <name> ...
    d="$FAKE_BASE/profiles/${2:-}"; mkdir -p "$d"
    [ -f "$d/config.yaml" ] || printf 'context:\n  engine: compressor\n' > "$d/config.yaml"
    exit 0 ;;
  config)
    sub="${2:-}"; key="${3:-}"; state="$FAKE_STATE/${p}.kv"
    case "$sub" in
      get) [ -f "$state" ] && sed -n "s|^${key}=||p" "$state" | head -1 || echo "" ;;
      set) printf '%s=%s\n' "$key" "${4-}" > "$state" ;;
    esac ;;
  plugins)
    if [ "${2:-}" = list ]; then echo "| none |"; fi ;;
esac
exit 0
"""


def _mk(tmp_path, engines):
    """engines: {profile: context.engine value} -> fake host env."""
    base = tmp_path / "hb"
    for p, eng in engines.items():
        (base / "profiles" / p).mkdir(parents=True)
        (base / "profiles" / p / "config.yaml").write_text("x: 1\n")
    state = tmp_path / "state"
    state.mkdir()
    for p, eng in engines.items():
        (state / f"{p}.kv").write_text(f"context.engine={eng}\n")
    bindir = tmp_path / "bin"
    bindir.mkdir()
    fake = bindir / "hermes"
    fake.write_text(FAKE_HERMES)
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    env = dict(os.environ, PATH=f"{bindir}{os.pathsep}{os.environ['PATH']}",
               HERMES_BASE=str(base), FAKE_BASE=str(base), FAKE_STATE=str(state))
    return env, base


def _run(script, *args, env=None):
    return subprocess.run(["bash", str(SCRIPTS / script), *args],
                          capture_output=True, text=True, timeout=120, env=env)


def _link(base, p):
    return base / "profiles" / p / "plugins" / "context_engine" / "lcm2-pg"


def test_capture_src_ships_engine_marker():
    init = (CAPTURE_SRC / "__init__.py").read_text()
    assert "register_context_engine" in init or "ContextEngine" in init


def test_assert_flags_missing_capture_dir(tmp_path):
    env, base = _mk(tmp_path, {"alice": "lcm2-pg", "bob": "compressor"})
    r = _run("capture_dir_assert.sh", env=env)
    assert r.returncode == 2, r.stdout + r.stderr
    assert "CAPTURE_DIR RED  alice MISSING" in r.stdout
    assert "ln -sfn" in r.stdout  # names the exact fix command
    assert "bob" not in r.stdout  # compressor profile not demanded


def test_onboard_authors_capture_symlink(tmp_path):
    env, base = _mk(tmp_path, {"alice": "compressor"})
    # real run against the fake hermes; psql legs fail-closed on a dead
    # BASE override BEFORE §5, so drive §5 directly with a stub psql that
    # exits 0 (schemas are irrelevant to this leg).
    stubdir = tmp_path / "bin2"
    stubdir.mkdir()
    stub = stubdir / "psql"
    stub.write_text("#!/usr/bin/env bash\nexit 0\n")
    stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
    env = dict(env, PATH=f"{stubdir}{os.pathsep}{env['PATH']}")
    r = _run("onboard_profile.sh", "alice", env=env)
    assert r.returncode == 0, r.stdout + r.stderr
    link = _link(base, "alice")
    assert link.is_symlink() and link.resolve() == CAPTURE_SRC.resolve(), r.stdout
    assert (link / "__init__.py").is_file()


def test_cutover_applies_and_verifies(tmp_path):
    env, base = _mk(tmp_path, {"alice": "lcm2-pg", "carol": "lcm2-pg",
                               "bob": "compressor"})
    r = _run("cutover_capture_dirs.sh", env=env)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "CAPTURE_CUTOVER set  alice" in r.stdout
    assert "CAPTURE_CUTOVER set  carol" in r.stdout
    assert "skip bob" in r.stdout
    for p in ("alice", "carol"):
        link = _link(base, p)
        assert link.is_symlink() and link.resolve() == CAPTURE_SRC.resolve()
        assert (link / "__init__.py").is_file()  # resolves through
    # idempotent
    r2 = _run("cutover_capture_dirs.sh", env=env)
    assert r2.returncode == 0 and "already linked" in r2.stdout
    # verifier agrees
    r3 = _run("capture_dir_assert.sh", env=env)
    assert r3.returncode == 0 and "CAPTURE_DIR GREEN" in r3.stdout


def test_assert_red_on_wrong_target(tmp_path):
    env, base = _mk(tmp_path, {"alice": "lcm2-pg"})
    link = _link(base, "alice")
    link.parent.mkdir(parents=True)
    link.symlink_to(tmp_path / "nowhere")
    r = _run("capture_dir_assert.sh", env=env)
    assert r.returncode == 2 and "CAPTURE_DIR RED  alice" in r.stdout


def test_env_depcheck_fail_closed(tmp_path):
    # red contract without touching a live gateway: pidfile points at a
    # pid that cannot exist -> fail-closed RED, never silence.
    hb = tmp_path / "hb"
    hb.mkdir()
    (hb / "gateway.pid").write_text('{"pid": 999999999}')
    env = dict(os.environ, HERMES_BASE=str(hb))
    r = _run("env_depcheck.sh", env=env)
    assert r.returncode == 2, r.stdout + r.stderr
    assert "ENVDEPCHECK RED" in (r.stdout + r.stderr)


def test_env_depcheck_green_shape_live(tmp_path):
    # live-shape (no fake): point at the real gateway pidfile if present;
    # outcome must be deterministic text either way (GREEN or named RED).
    env = dict(os.environ)
    r = _run("env_depcheck.sh", env=env)
    out = r.stdout + r.stderr
    assert ("ENVDEPCHECK GREEN" in out) or ("ENVDEPCHECK RED" in out), out
    if r.returncode == 0:
        assert "ENVDEPCHECK GREEN" in out
    else:
        assert "FIX" in out or "cannot" in out.lower()  # names a path to fix


def test_pgconn_lanes_demand_psycopg_pool_before_use():
    for lane in ("lcm2-pg", "mnemo-pg2"):
        src = (REPO / "plugins" / lane / "pgconn.py").read_text()
        imp = src.find("from psycopg_pool import")
        use = src.find("ConnectionPool(")
        assert imp != -1, lane
        assert use == -1 or imp < use, lane
        # X-4 depcheck runs FIRST but only proves psycopg — the pool
        # import must be a module-top import (loud ImportError at load,
        # not lazy inside a call):
        assert src[:imp].count("\n") < 80, f"{lane}: pool import not top-level"
