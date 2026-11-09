"""get() row-shape (runtime drift 360ad3b, upstreamed 2026-11-09).

Engine.get() ran _jsonable(row[0]) — a single DICT — while _jsonable
expects the LIST OF DICTS query_dicts returns; every memory_get raised
AttributeError on the live host. The fix (buddyos-nexa runtime commit
360ad3b: _jsonable(row)[0]) never reached origin/main, so the crash
shipped to every fresh rollout. This is the reproducer.

Binding: the repo-sanctioned shape (p4x-class) — the dir's _src_window
hook swaps THIS module's `pgconn` attribute to our own file around each
test, and engine._cx resolves pgconn dynamically through it. So:
`import engine` namespace-style (not `from engine import Engine` at
collection), and no reload tricks (reload rebinds engine to whichever
bare key the lcm2 suite left — kind= TypeError; template+clone 2026-11-09).
"""
import engine as _engine_mod  # namespace import: hook swaps .pgconn per-test
import uuid


def test_get_roundtrip_no_attrerror(dsn):
    e = _engine_mod.Engine(dsn=dsn)
    probe = "get row-shape probe " + uuid.uuid4().hex[:8]
    r = e.remember(probe, importance=0.4)
    assert r.get("ok"), r
    # remember() mints ids ITSELF (ignores caller-passed ids) — read the
    # returned one; a hand-passed id can never be the stored row.
    g = e.get(r["id"])
    assert g.get("ok") is True, g
    assert g["memory"]["content"] == probe
    assert g["memory"]["id"] == r["id"]


def test_get_missing_returns_not_found(dsn):
    e = _engine_mod.Engine(dsn=dsn)
    g = e.get("mem-ffffffff-does-not-exist")
    assert g == {"ok": False, "error": "not found", "memory": None}
