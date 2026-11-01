"""Boot-stress regression (QA-r3 rec #3, QA-r4 F-2): N short host boots of
the lcm2agent profile must all exit 0 with no import poisoning crash.

Closes the loop on the P2FLIP3 fix (05376ec): anchor() must never take/
publish/reinstate a bare sys.modules key the host owns as a PACKAGE, which
was the '~1 in 24 boots: tools is not a package' race. tessa's r4 stress
was 19/19 manual; this pins the same gate into the suite so a regression
fails CI, not the next QA round.

Serial by design: the r3 race was concurrency in the *imports* (both plugin
loaders racing inside one boot), not load on the box, and tessa's own r4
gate (G1a) was sequential too. Marked slow (N x ~18s boot wall);
LCM2_BOOT_N overrides the count, LCM2_SKIP_BOOT_STRESS=1 skips (e.g. CI
without the profile).
"""
import os
import pathlib
import shutil
import subprocess

import pytest

REPO = pathlib.Path(__file__).resolve().parents[2]
PROFILE = os.environ.get("LCM2_BOOT_PROFILE", "lcm2agent")
N = int(os.environ.get("LCM2_BOOT_N", "6"))
BOOT_TIMEOUT = 180

# crash fingerprints of the closed race (import poisoning), NOT the
# adjudicated F-4 PythonFinalizationError shutdown noise (present on every
# exit-0 boot; QA-r4 F-4) and not the model/gateway noise a -q boot may log.
CRASH_PATTERNS = (
    "ModuleNotFoundError",
    "is not a package",
    "ImportError",
)

pytestmark = pytest.mark.skipif(
    os.environ.get("LCM2_SKIP_BOOT_STRESS") == "1"
    or shutil.which("hermes") is None
    or not pathlib.Path.home().joinpath(
        ".hermes", "profiles", PROFILE).is_dir(),
    reason="boot stress needs the `hermes` CLI and the "
           f"~/.hermes/profiles/{PROFILE} profile",
)


def test_boot_stress_no_crashes():
    hermes = shutil.which("hermes")
    assert hermes  # skipif guarantees it; narrows the type for the run below
    crashes = []
    for i in range(N):
        # no --resume: every boot starts a fresh session id (host default),
        # so no state carries between boots
        try:
            r = subprocess.run(
                [hermes, "-p", PROFILE, "chat", "-q", "Reply PONG"],
                capture_output=True, text=True, timeout=BOOT_TIMEOUT,
                cwd=str(REPO),
            )
        except subprocess.TimeoutExpired:
            crashes.append(f"boot {i}: TIMEOUT after {BOOT_TIMEOUT}s")
            continue
        err = r.stderr or ""
        hits = [p for p in CRASH_PATTERNS if p in err]
        if r.returncode != 0 or hits:
            crashes.append(
                f"boot {i}: rc={r.returncode} patterns={hits} "
                f"stderr_tail={err[-500:]!r}")
    assert not crashes, "boot stress crashed:\n" + "\n".join(crashes)
