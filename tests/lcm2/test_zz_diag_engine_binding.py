"""qa5 probe: run under tests/lcm2 only; print which file `engine` binds to.

Run:  pytest tests/lcm2/test_zz_diag_engine_binding.py -q -s -p no:randomly
Also meaningful in a combined run. Diagnostic only; delete after qa5.
"""
import sys


def test_diag_engine_binding():
    eng = sys.modules.get("engine")
    print(f"\nDIAG engine file: {getattr(eng, '__file__', None)!r}")
    print(f"DIAG has LCM2Engine: {hasattr(eng, 'LCM2Engine')}")
    print(f"DIAG has MnemoEngine-ish: {[a for a in dir(eng) if 'Engine' in a]}")
    # every sys.modules entry pointing into either plugin dir
    for name, mod in sorted(sys.modules.items()):
        f = getattr(mod, "__file__", None) or ""
        if "/plugins/lcm2-pg/" in f or "/plugins/mnemo-pg2/" in f:
            print(f"DIAG sys.modules[{name!r}] -> {f}")
    assert True
