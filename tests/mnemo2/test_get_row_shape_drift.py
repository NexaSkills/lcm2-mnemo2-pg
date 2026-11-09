"""get() row-shape (runtime drift 360ad3b, upstreamed 2026-11-09).

Engine.get() ran _jsonable(row[0]) — a single DICT — while _jsonable
expects the LIST OF DICTS query_dicts returns; every memory_get raised
AttributeError on the live host. The fix (buddyos-nexa runtime commit
360ad3b: _jsonable(row)[0]) never reached origin/main, so the crash
shipped to every fresh rollout. This is the reproducer: remember + get
must return the row, not raise.
"""
from engine import Engine  # noqa: E402  (via conftest sys.path)


def test_get_roundtrip_no_attrerror(dsn):
    eng = Engine(dsn=dsn)
    r = eng.remember("get row-shape probe", importance=0.4)
    assert r.get("ok"), r
    mid = r["id"]
    g = eng.get(mid)
    assert g.get("ok") is True, g
    assert g["memory"]["content"] == "get row-shape probe"
    assert g["memory"]["id"] == mid


def test_get_missing_returns_not_found(dsn):
    eng = Engine(dsn=dsn)
    g = eng.get("mem-does-not-exist-at-all")
    assert g == {"ok": False, "error": "not found", "memory": None}
