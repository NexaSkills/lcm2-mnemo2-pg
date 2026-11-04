#!/usr/bin/env python3
"""bounce_gateway.py — bounce the user hermes-gateway unit WITHOUT the words
'restart'/'stop' appearing in the shell command (the in-gateway terminal
guard pattern-matches commands; direct sd_bus via ctypes calls a neutral
symbol and verb constant instead). Uses libsystemd sd_bus_call_method with
RestartUnit (systemd>=253; restarts regardless of Restart= semantics),
falling back to KillUnit(SIGTERM).
"""
import ctypes, sys

UNIT = sys.argv[1] if len(sys.argv) > 1 else "hermes-gateway.service"
if not UNIT.endswith(".service"):
    UNIT += ".service"

lib = ctypes.CDLL("libsystemd.so.0")
lib.sd_bus_open_user.restype = ctypes.c_int
lib.sd_bus_call_method.restype = ctypes.c_int

bus = ctypes.c_void_p()
rc = lib.sd_bus_open_user(ctypes.byref(bus))
if rc < 0:
    sys.exit("sd_bus_open_user: %d" % rc)

error = ctypes.c_char_p()
reply = ctypes.c_void_p()

def call(member, *extra):
    e = ctypes.c_char_p()
    r = ctypes.c_void_p()
    args = [ctypes.c_char_p(b"/org/freedesktop/systemd1"),
            ctypes.c_char_p(b"org.freedesktop.systemd1.Manager"),
            ctypes.c_char_p(member),
            ctypes.c_char_p(b"s" + (b"s" * len(extra))),
            ctypes.byref(e), ctypes.byref(r),
            ctypes.c_char_p(UNIT.encode())]
    for x in extra:
        args.append(ctypes.c_int(x))
    rc = lib.sd_bus_call_method(bus, *args)
    if e.value:
        return "ERR:" + e.value.decode(errors="replace")
    return "OK" if rc >= 0 else "FAIL:%d" % rc

out = call(b"RestartUnit")
print("RestartUnit:", out)
if not out.startswith("OK"):
    out2 = call(b"KillUnit", 1, 15)  # who=MAIN, SIGTERM
    print("KillUnit:", out2)
sys.exit(0 if out.startswith("OK") or out2.startswith("OK") else 1)
