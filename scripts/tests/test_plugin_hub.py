"""plugin_hub.sh self-test (Jan's standard 2026-11-11): all plugins RESIDE in
$HERMES_HOME/plugins as REAL directories (rsynced from the checkout), every
non-default profile gets SYMLINKS into that hub, and on self-contained hosts
the git checkout itself lives under $HERMES_HOME/checkout. The script must be
idempotent, propagate checkout edits (rsync), take over legacy hub symlinks
that pointed outside ~/.hermes, and fail closed on foreign hub entries.

Sandboxed via env overrides — never touches the real ~/.hermes:
  PLUGIN_HUB_REPO      current checkout path   (legacy default: script dir/..)
  PLUGIN_HUB_DEST      relocate destination    (default $HERMES_HOME/checkout/<repo>)
  PLUGIN_HUB_RELOCATE  1 = relocate leg active
  HERMES_HOME          fake hermes home
"""
import os
import pathlib
import shutil
import subprocess

import pytest

SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "plugin_hub.sh"


def _run(env, *args):
    return subprocess.run(["bash", str(SCRIPT), *args], env=env,
                          capture_output=True, text=True, timeout=60)


@pytest.fixture()
def sandbox(tmp_path):
    if shutil.which("rsync") is None:
        pytest.skip("rsync required")
    home = tmp_path / "hermes"
    repo = tmp_path / "ops" / "lcm2-mnemo2-pg"  # legacy location OUTSIDE ~/.hermes
    for p in ("plugins/lcm2-pg", "plugins/mnemo-pg2", "plugins/mnemosyne",
              "plugins/context_engine/lcm2-pg", "scripts"):
        (repo / p).mkdir(parents=True)
    (repo / "plugins/lcm2-pg/engine.py").write_text("v1\n")
    (repo / "plugins/mnemo-pg2/__init__.py").write_text("m1\n")
    (repo / "plugins/mnemosyne/plugin.py").write_text("b1\n")
    (repo / "plugins/context_engine/lcm2-pg/loader.py").write_text("c1\n")
    # scripts/ dir so the script can locate a repo root
    (repo / "scripts/plugin_hub.sh").write_text("x\n")
    home.mkdir()
    env = dict(os.environ, PLUGIN_HUB_HOME=str(home),
               HERMES_HOME=str(home / "profile-scoped-should-be-ignored"),
               PLUGIN_HUB_REPO=str(repo),
               PLUGIN_HUB_DEST=str(home / "checkout" / "lcm2-mnemo2-pg"),
               PLUGIN_HUB_RELOCATE="1")
    # legacy layout: hub symlinks pointing at the old checkout, one profile
    hub = home / "plugins"
    (hub / "context_engine").mkdir(parents=True)
    for n in ("lcm2-pg", "mnemo-pg2", "mnemosyne"):
        (hub / n).symlink_to(repo / "plugins" / n)
    (hub / "context_engine" / "lcm2-pg").symlink_to(repo / "plugins" / "context_engine" / "lcm2-pg")
    prof = home / "profiles" / "alice" / "plugins"
    prof.mkdir(parents=True)
    for n in ("lcm2-pg", "mnemo-pg2"):
        (prof / n).symlink_to(repo / "plugins" / n)  # legacy: straight to checkout
    return env, home, repo


def test_relocate_and_materialize(sandbox):
    env, home, repo = sandbox
    r = _run(env)
    assert r.returncode == 0, r.stdout + r.stderr
    assert (home / "checkout/lcm2-mnemo2-pg").is_dir()
    assert not repo.exists(), "old legacy checkout path must be gone after move"
    hub = home / "plugins"
    for n in ("lcm2-pg", "mnemo-pg2", "mnemosyne"):
        assert (hub / n).is_dir() and not (hub / n).is_symlink(), n
    assert (hub / "lcm2-pg/engine.py").read_text() == "v1\n"
    assert (hub / "context_engine/lcm2-pg").is_symlink()
    assert os.readlink(hub / "context_engine/lcm2-pg").endswith(
        "checkout/lcm2-mnemo2-pg/plugins/context_engine/lcm2-pg")
    for n in ("lcm2-pg", "mnemo-pg2"):
        assert os.readlink(home / "profiles/alice/plugins" / n) == str(hub / n)


def test_idempotent_and_rsync_propagates(sandbox):
    env, home, repo = sandbox
    assert _run(env).returncode == 0
    r2 = _run(env)
    assert r2.returncode == 0, r2.stdout + r2.stderr
    assert "NOCHANGE" in r2.stdout + r2.stderr or "OK" in r2.stdout
    src = home / "checkout/lcm2-mnemo2-pg/plugins/lcm2-pg/engine.py"
    src.write_text("v2\n")
    r3 = _run(env)
    assert r3.returncode == 0, r3.stdout + r3.stderr
    assert (home / "plugins/lcm2-pg/engine.py").read_text() == "v2\n", \
        "hub stale after checkout edit; run3 output:\n" + r3.stdout + r3.stderr


def test_check_mode_and_foreign_entry(sandbox):
    env, home, repo = sandbox
    assert _run(env).returncode == 0
    c = _run(env, "--check")
    assert c.returncode == 0, c.stdout + c.stderr
    # drift: hub diverges from checkout -> --check must fail closed
    (home / "plugins/lcm2-pg/engine.py").write_text("rogue\n")
    c2 = _run(env, "--check")
    assert c2.returncode != 0
    assert "FAIL" in c2.stdout + c2.stderr


def test_fail_closed_on_foreign_hub_entry(sandbox):
    env, home, repo = sandbox
    foreign = home / "vendor-plugin"
    (foreign / "x").parent.mkdir(parents=True, exist_ok=True)
    # hub mnemosyne is a symlink to a NON-repo path: takeover refused
    (home / "plugins/mnemosyne").unlink()
    (home / "plugins/mnemosyne").symlink_to(foreign)
    r = _run(env)
    assert r.returncode != 0
    assert "FAIL" in r.stdout + r.stderr
    assert foreign.exists()
