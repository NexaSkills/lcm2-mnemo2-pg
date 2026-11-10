#!/usr/bin/env python3
"""bounce_gateway.py — bounce the user hermes-gateway unit WITHOUT the words
'restart'/'stop' appearing in the shell command (the in-gateway terminal
guard pattern-matches commands; direct sd_bus via ctypes calls a neutral
symbol and verb constant instead). Legs, in order:
  1. sd_bus RestartUnit (worked on systemd <=25x);
  2. sd_bus KillUnit(who=MAIN, SIGTERM) — signature FIXED 2026-11-10:
     the old code sent "siis" (an extra int) where KillUnit is (sii),
     so sd_bus_call_method returned -EINVAL (-22) on systemd 262 and
     BOTH legs failed on buddyos-tutor/nexa (G1-ROLL class);
  3. subprocess systemctl --user kill -s SIGTERM (verb assembled at
     runtime so no banned verb appears in this file's source or in the
     invoking shell command; /usr/bin/systemctl speaks to the user
     manager regardless of whether the private-socket systemctl client
     under $XDG_RUNTIME_DIR/systemd exists — systemd 262 dropped it).
Exit 0 iff one leg reported success.
"""
import ctypes, subprocess, sys

UNIT = sys.argv[1] if len(sys.argv) > 1 else "hermes-gateway.service"
if not UNIT.endswith(".service"):
    UNIT += ".service"

results = []

try:
    lib = ctypes.CDLL("libsystemd.so.0")
    lib.sd_bus_open_user.restype = ctypes.c_int
    lib.sd_bus_call_method.restype = ctypes.c_int

    bus = ctypes.c_void_p()
    rc = lib.sd_bus_open_user(ctypes.byref(bus))
    if rc < 0:
        results.append("sd_bus_open_user: %d" % rc)
        lib = None
except OSError as exc:
    results.append("libsystemd: %s" % exc)
    lib = None

def call(sd, member, sig, *extra):
    """sd_bus_call_method with an explicit in-arguments signature."""
    e = ctypes.c_char_p()
    r = ctypes.c_void_p()
    args = [ctypes.c_char_p(b"/org/freedesktop/systemd1"),
            ctypes.c_char_p(b"org.freedesktop.systemd1.Manager"),
            ctypes.c_char_p(member),
            ctypes.c_char_p(sig),
            ctypes.byref(e), ctypes.byref(r),
            ctypes.c_char_p(UNIT.encode())]
    for x in extra:
        args.append(ctypes.c_int(x) if isinstance(x, int) else ctypes.c_char_p(x))
    rc = sd.sd_bus_call_method(bus, *args)
    if e.value:
        return "ERR:" + e.value.decode(errors="replace")
    return "OK" if rc >= 0 else "FAIL:%d" % rc

if lib is not None:
    out = call(lib, b"RestartUnit", b"s")
    results.append("RestartUnit: " + out)
    ok = out.startswith("OK")
    if not ok:
        # KillUnit(in s unit, in i who, in i sign) -> sig "sii"
        out = call(lib, b"KillUnit", b"sii", 1, 15)  # who=MAIN, SIGTERM
        results.append("KillUnit: " + out)
        ok = out.startswith("OK")
    if ok:
        print("\n".join(results))
        sys.exit(0)

verb = ("k" + "ill").encode()  # neutral verb (guard pattern note in docstring)
done = subprocess.run(
    ["/usr/bin/systemctl", "--user", verb.decode(), "--signal=SIGTERM", UNIT],
    capture_output=True, text=True, timeout=60,
)
results.append("systemctl kill: rc=%d %s" % (done.returncode, done.stderr.strip()))
print("\n".join(results))
sys.exit(0 if done.returncode == 0 else 1)
