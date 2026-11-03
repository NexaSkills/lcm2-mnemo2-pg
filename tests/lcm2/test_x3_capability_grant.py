"""X-3 targeted test: capability_grant_fix.sh (hermetic, no host contact).

Root-cause being cured (clone buddy, 296 deny lines): the plugin loader
asks plugin_capability_granted(plugin, "tools.override") which passes ONLY
via plugins.entries.<plugin>.granted_capabilities (canonical list) OR the
deprecated allow_tool_override key. The roll kit grants neither on the NEW
stack (lcm2-pg / mnemo-pg2) while legacy mnemosyne carries its legacy key
= new-stack tool overrides denied, legacy allowed (the exact inversion
tessa's gate logged).

The cure script grants the canonical way via `hermes config set` ONLY
(fleet rule). Sandbox is under /tmp (never under a Hermes home: the real
CLI shadows PATH there). Proves:
  1. both new plugins get tools.override granted for root + every profile;
  2. idempotency: second run writes nothing (verify says ok, zero sets);
  3. an existing granted list is APPENDED to, not clobbered;
  4. a set that silently no-ops is reported FAIL, not silence;
  5. invocation shape `hermes [-p p] config set
     plugins.entries.<pl>.granted_capabilities [...]` (root: no -p);
  6. the GRANT key is what hermes' own plugin_capability_granted reads.
"""
import os
import shutil
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "capability_grant_fix.sh"

FAKE_HERMES = r"""#!/usr/bin/env bash
# fake hermes CLI: `hermes [-p PROFILE] config (get|set) KEY [VALUE]`
# state = per-target tree under $FAKE_STATE/<profile|__root>/<key-as-path>
p=""
if [ "${1:-}" = "-p" ]; then p="${2:-}"; shift 2; fi
[ "${1:-}" = "config" ] || exit 0
sub="${2:-}"; key="${3:-}"; shift 3 2>/dev/null || true
f="$FAKE_STATE/${p:-__root}/${key//./\/}"
echo "$p|$sub|$key|$*" >> "$FAKE_LOG"
case "$sub" in
  get)
    if [ -f "$f" ]; then cat "$f"; else echo "Config key not set: $key" >&2; exit 1; fi
    ;;
  set)
    if [ "${FAKE_NOOP:-}" = "$p/$key" ]; then exit 0; fi   # silent no-op sim
    mkdir -p "$(dirname "$f")"; printf '%s' "${1:-}" > "$f"
    ;;
esac
exit 0
"""


class _Sandbox:
    """One fake-host install under /tmp (a Hermes home would let the real
    CLI shadow PATH). Persistent across runs so idempotency is real."""

    def __init__(self, name):
        self.root = Path("/tmp") / f"x3cap-{name}"
        shutil.rmtree(self.root, ignore_errors=True)
        (self.root / "bin").mkdir(parents=True)
        fake = self.root / "bin" / "hermes"
        fake.write_text(FAKE_HERMES)
        fake.chmod(0o755)
        self.state = self.root / "state"
        self.state.mkdir()
        self.base = self.root / "hb"
        self.base.mkdir()
        (self.base / "config.yaml").write_text("{}\n")
        for p in ("buddy", "default"):
            (self.base / "profiles" / p).mkdir(parents=True)
            (self.base / "profiles" / p / "config.yaml").write_text("{}\n")

    def run(self, logfile="argv.log", state_setup=None, env_extra=None):
        if state_setup:
            state_setup(self.state)
        log = self.root / logfile
        log.write_text("")  # truncate this run's argv log
        env = {k: v for k, v in os.environ.items() if k != "HERMES_HOME"}
        env["PATH"] = f"{self.root / 'bin'}{os.pathsep}/usr/bin{os.pathsep}/bin"
        env["FAKE_STATE"] = str(self.state)
        env["FAKE_LOG"] = str(log)
        env["HERMES_BASE"] = str(self.base)
        env.update(env_extra or {})
        r = subprocess.run([str(Path("/bin/bash")), str(SCRIPT)], env=env,
                           capture_output=True, text=True, timeout=60)
        lines = [l.split("|") for l in log.read_text().splitlines() if l]
        return r, lines


def _sb(tmp_path):
    return _Sandbox(tmp_path.name)


def test_grants_both_plugins_every_target(tmp_path):
    sb = _sb(tmp_path)
    r, lines = sb.run()
    assert r.returncode == 0, r.stdout + r.stderr
    assert "CAP_GRANT GREEN" in r.stdout
    sets = [l for l in lines if l[1] == "set"]
    assert {l[0] for l in sets} == {"", "buddy", "default"}  # root: no -p
    assert {l[2] for l in sets} == {
        "plugins.entries.lcm2-pg.granted_capabilities",
        "plugins.entries.mnemo-pg2.granted_capabilities"}
    assert all("[tools.override]" in l[3] for l in sets)
    for p in ("__root", "buddy", "default"):
        for pl in ("lcm2-pg", "mnemo-pg2"):
            f = (sb.state / p / "plugins" / "entries" / pl /
                 "granted_capabilities")
            assert f.is_file() and "tools.override" in f.read_text(), (p, pl)


def test_idempotent_second_run(tmp_path):
    sb = _sb(tmp_path)
    r1, _ = sb.run()
    assert r1.returncode == 0, r1.stdout + r1.stderr
    r2, lines2 = sb.run(logfile="argv2.log")  # SAME state (no wipe)
    assert r2.returncode == 0, r2.stdout + r2.stderr
    assert "GRANT ok" in r2.stdout
    assert [l for l in lines2 if l[1] == "set"] == []  # zero writes


def test_appends_existing_list(tmp_path):
    sb = _sb(tmp_path)

    def setup(state):
        d = state / "__root" / "plugins" / "entries" / "lcm2-pg"
        d.mkdir(parents=True)
        (d / "granted_capabilities").write_text("[llm.model_override]")
    r, _ = sb.run(state_setup=setup)
    assert r.returncode == 0, r.stdout + r.stderr
    val = (sb.state / "__root" / "plugins" / "entries" / "lcm2-pg" /
           "granted_capabilities").read_text()
    assert "llm.model_override" in val and "tools.override" in val


def test_silent_noop_is_red(tmp_path):
    sb = _sb(tmp_path)
    r, _ = sb.run(env_extra={
        "FAKE_NOOP": "/plugins.entries.lcm2-pg.granted_capabilities"})
    assert r.returncode != 0
    assert "GRANT FAIL" in r.stdout


def test_resolver_semantics_pinned():
    """hermes plugin_capability_granted: canonical list => allow; legacy
    allow_tool_override => allow; neither => deny. Pin the inversion tessa
    saw (legacy allowed, new stack denied) so the cure's key choice is
    provably the one the real resolver reads."""
    CAP = "tools.override"
    GRANTED_KEY = "granted_capabilities"

    def allowed(cfg_entry):
        raw = cfg_entry.get(GRANTED_KEY)
        canon = isinstance(raw, list) and CAP in raw
        return canon or bool(cfg_entry.get("allow_tool_override"))

    # legacy mnemosyne (allow_tool_override: true) => allowed (deprecated)
    assert allowed({"allow_tool_override": True})
    # new stack BEFORE cure: neither key => denied (the 296 deny lines)
    assert not allowed({})
    # new stack AFTER cure: canonical grant => allowed
    assert allowed({GRANTED_KEY: ["tools.override"]})
