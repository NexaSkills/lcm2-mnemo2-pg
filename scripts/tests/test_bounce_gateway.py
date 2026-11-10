"""bounce_gateway.py self-test (G1-ROLL class, 2026-11-10 buddyos-tutor):
the sd_bus RestartUnit/KillUnit legs return -22 (EINVAL) on systemd 262
(verified on buddyos-tutor, systemd 262.1-1-arch), and the sibling
bounce_gateway.sh hardcodes /run/user/$UID/systemd/systemctl which that
systemd no longer ships. A host where BOTH legs fail is undebounceable
by the repo tooling (rollout blocker: a freshly installed launcher-env
dependency never reaches the resident gateway process).

This test bounces a THROWAWAY user unit (never hermes-gateway) and
asserts the script reports success via some leg.
"""
import os
import pathlib
import subprocess
import time

import pytest

SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "bounce_gateway.py"
UNIT = "hermes-bounce-selftest"


def _systemctl(*args):
    return subprocess.run(
        ["systemctl", "--user", *args],
        capture_output=True, text=True, timeout=30,
    )


@pytest.fixture()
def throwaway_unit():
    if subprocess.run(["systemctl", "--user", "show", "-p", "LoadState", UNIT],
                      capture_output=True, text=True).stdout.strip() != "LoadState=not-found":
        pytest.skip("selftest unit name already taken on this host")
    unit_dir = os.path.expanduser("~/.config/systemd/user")
    os.makedirs(unit_dir, exist_ok=True)
    path = os.path.join(unit_dir, UNIT + ".service")
    with open(path, "w") as fh:
        fh.write("[Service]\nExecStart=/bin/sleep 600\n"
                 "Restart=always\n")
    _systemctl("daemon-reload")
    try:
        yield path
    finally:
        _systemctl("disable", "--now", UNIT + ".service")
        os.remove(path)
        _systemctl("daemon-reload")


def test_bounce_client_works_on_this_systemd(throwaway_unit):
    start = _systemctl("start", UNIT + ".service")
    assert start.returncode == 0, start.stderr
    time.sleep(0.5)
    pid_before = _systemctl("show", UNIT + ".service", "-p", "MainPID").stdout
    out = subprocess.run(
        ["python3", str(SCRIPT), UNIT + ".service"],
        capture_output=True, text=True, timeout=60,
    )
    assert out.returncode == 0, (out.stdout, out.stderr)
    time.sleep(1.0)
    pid_after = _systemctl("show", UNIT + ".service", "-p", "MainPID").stdout
    assert pid_after != pid_before, "unit survived the bounce unchanged"
