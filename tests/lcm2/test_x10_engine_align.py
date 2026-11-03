"""X-10 targeted test: engine_engine_align.sh (hermetic, no host contact).

Root-cause being cured (counters static fleet-wide, debbie rebattery-close
PARTIAL driver): after the lcm2 roll, context.engine still says "lcm" on
rolled hosts, so the legacy lcm-pg engine wins the host's ONE
context-engine slot; with the old :54329 instance torn down its ingest
fails and lcm2_<p>.messages/summary_nodes never advance.

The cure script flips context.engine -> lcm2-pg via `hermes config set`
ONLY (fleet rule: never file-edit config.yaml). This test runs the script
against a FAKE hermes on PATH (no real host CLI touched) and proves:
  1. every target (root + profiles) flips and the verify leg says GREEN;
  2. a set that silently no-ops is reported as FAIL, not silence
     (never-trust-the-surface);
  3. idempotency: second run = all ok, zero further set calls;
  4. invocation shape is `hermes [-p p] config set context.engine
     lcm2-pg` (CLI-only rule, auditable from the fake's argv log; root
     target carries no -p).
"""
import os
import re
import stat
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "engine_engine_align.sh"

FAKE = r"""#!/usr/bin/env bash
# fake hermes: `hermes [-p PROFILE] config (get|set) context.engine [VALUE]`
# state = one flat key-value file per target; argv log = one line per call.
p=""
if [ "${1:-}" = "-p" ]; then p="${2:-}"; shift 2; fi
[ "${1:-}" = "config" ] || exit 0
sub="${2:-}"; key="${3:-}"; shift 3 2>/dev/null || true
state="$FAKE_STATE/${p:-root}.kv"; log="$FAKE_LOG"
echo "$p|$sub|$key|$*" >> "$log"
case "$sub" in
  get)
    if [ -f "$state" ]; then
      sed -n "s/^${key}=//p" "$state"
    else
      echo ""
    fi ;;
  set)
    if [ -n "$FAKE_NOOP" ] && [ "${p:-root}" = "$FAKE_NOOP" ]; then exit 0; fi
    printf '%s=%s\n' "$key" "${*%$'\n'}" > "$state" ;;
esac
exit 0
"""


def _mk(tmp_path, noop=""):
    base = tmp_path / "hb"
    for p in ("alpha", "buddy"):
        (base / "profiles" / p).mkdir(parents=True)
        (base / "profiles" / p / "config.yaml").write_text("context:\n  engine: lcm\n")
    (base / "config.yaml").write_text("context:\n  engine: lcm\n")
    state = tmp_path / "state"
    state.mkdir()
    for name in ("alpha", "buddy", "root"):
        (state / f"{name}.kv").write_text("context.engine=lcm\n")
    bindir = tmp_path / "bin"
    bindir.mkdir()
    fake = bindir / "hermes"
    fake.write_text(FAKE)
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    log = tmp_path / "calls.log"
    log.write_text("")
    env = dict(os.environ, PATH=f"{bindir}{os.pathsep}{os.environ['PATH']}",
               HERMES_BASE=str(base), FAKE_STATE=str(state), FAKE_LOG=str(log))
    if noop:
        env["FAKE_NOOP"] = noop
    return env


def _run(env):
    return subprocess.run(["bash", str(SCRIPT)], env=env, capture_output=True,
                          text=True, timeout=60)


def _val(env, target):
    txt = (Path(env["FAKE_STATE"]) / f"{target}.kv").read_text()
    m = re.search(r"^context\.engine=(.*)$", txt, re.M)
    return (m.group(1).strip() if m else "")


def test_flip_and_verify(tmp_path):
    env = _mk(tmp_path)
    r = _run(env)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "ENGINE_ALIGN GREEN" in r.stdout
    for p in ("root", "alpha", "buddy"):
        assert f"ALIGN set  {p} (lcm -> lcm2-pg)" in r.stdout, r.stdout
        assert _val(env, p) == "lcm2-pg"


def test_silent_noop_fails_loud(tmp_path):
    env = _mk(tmp_path, noop="alpha")
    r = _run(env)
    assert r.returncode != 0
    assert "ALIGN FAIL alpha" in r.stdout
    assert "ENGINE_ALIGN RED" in r.stdout


def test_idempotent(tmp_path):
    env = _mk(tmp_path)
    _run(env)
    before = sum(1 for l in Path(env["FAKE_LOG"]).read_text().splitlines()
                 if "|set|" in l)
    r = _run(env)
    assert r.returncode == 0 and "ENGINE_ALIGN GREEN" in r.stdout
    assert "ALIGN ok" in r.stdout
    after = sum(1 for l in Path(env["FAKE_LOG"]).read_text().splitlines()
                if "|set|" in l)
    assert after == before  # second run: get-only, zero set calls


def test_cli_argv_shape(tmp_path):
    env = _mk(tmp_path)
    _run(env)
    lines = [l.split("|") for l in Path(env["FAKE_LOG"]).read_text().splitlines() if l]
    sets = [l for l in lines if l[1] == "set"]
    assert sets
    # every set addresses exactly the key and the value positionally
    assert all(l[2] == "context.engine" and l[3] == "lcm2-pg" for l in sets)
    # profile targets carry -p (fake recorded p), root target recorded p=""
    assert any(l[0] == "alpha" for l in sets) and any(l[0] == "" for l in sets)
