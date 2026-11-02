"""F-1 (pgconn hijack, probe_writer6): anchor() must serve OUR file even
when the bare key is already held by a FOREIGN module.

Defect (post-boot / live-session world): lcm2-pg booted first and left
sys.modules['pgconn'] bound to lcm2's file. anchor('pgconn') then took
the fast read path — "the key exists and there is no live exec in
flight, so the module is fully executed" — but only checked the key and
its OWN identity registry. A foreign binding is fully executed too; it
is just not OURS. Every later `import pgconn` inside mnemo-pg2's engine
ran lcm2's connect()/agent_schema (TypeError on `kind`, lcm2_* schemas —
the live mnemo2agent / r7-order failure class).

Fix: the read path requires the candidate be OURS (_mine, the same
identity stamp _ready_module uses). A foreign-bound key falls through to
the exec path, which rebinds under our namespaced + package keys and
(legitimately, via the existing publish gate) the bare key, restoring
the foreign module through the eviction bookkeeping exactly as before.
"""
import sys

import _selfimport as si  # bare, in-test world


def test_anchor_read_path_rejects_foreign_binding(monkeypatch):
    """Simulate lcm2 owning the bare key; anchor must return OUR file."""
    import types
    import importlib.util as ilu
    import os

    fake = types.ModuleType("pgconn")
    fake.__file__ = "/tmp/NOT-OURS-fake/pgconn.py"
    # Pre-existing (not newly created) foreign binding, like a boot world.
    saved = sys.modules.get("pgconn")
    sys.modules["pgconn"] = fake
    try:
        mod = si.anchor("pgconn")
        f = getattr(mod, "__file__", "")
        assert os.path.dirname(os.path.realpath(f)) == os.path.dirname(
            os.path.realpath(si.__file__)), (
            "anchor() returned a FOREIGN module for a bare key already "
            f"held by another package: {f}")
    finally:
        if saved is None:
            sys.modules.pop("pgconn", None)
        else:
            sys.modules["pgconn"] = saved
