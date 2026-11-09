"""Bounded pool cache (2026-11-09 suite-green hunt).

Full-directory runs create 150+ distinct conninfos; the unbounded cache
parked min-size connections per dead pool and the cluster hit
'FATAL: sorry, too many clients already' -> PoolTimeout storms (r8/r4/h2,
full-dir-only signature). _get_pool must now evict oldest-first past
_MAX_POOLS. Hermetic: fake ConnectionPool recorder, no real DB.
"""
import importlib
import sys
import types


class _FakePool:
    instances = []

    def __init__(self, conninfo, **kw):
        self.conninfo = conninfo
        self.shrunk = 0
        _FakePool.instances.append(self)

    def resize(self, n=0, max_size=None):
        self.shrunk += 1

    def close(self, timeout=None):
        self.shrunk += 999  # marker: CLOSED (eviction must close, not resize)


def _load_pgconn(monkeypatch, tmp_path):
    name = f"_poolcap_pgconn_{id(tmp_path)}"
    import pgconn as real  # the suite-loaded sibling
    mod = types.ModuleType(name)
    src = open(real.__file__).read()
    assert "_MAX_POOLS" in src, "bounded cache missing from pgconn"
    mod.__file__ = real.__file__
    mod.__dict__["__name__"] = name
    exec(compile(src, real.__file__, "exec"), mod.__dict__)
    monkeypatch.setattr(mod, "ConnectionPool", _FakePool)
    monkeypatch.setattr(mod, "_ensure_ext_in_pg_catalog", lambda ci: None)
    return mod


def test_eviction_bounds_cache(monkeypatch, tmp_path):
    m = _load_pgconn(monkeypatch, tmp_path)
    _FakePool.instances.clear()
    m._pools.clear()
    for i in range(m._MAX_POOLS + 5):
        m._get_pool(f"dbname=fake host=/tmp/pc{i}")
    assert len(m._pools) == m._MAX_POOLS, len(m._pools)
    shrunk = [p for p in _FakePool.instances if p.shrunk]
    assert len(shrunk) == 5, f"evicted pools must be shrunk: {len(shrunk)}"
    assert shrunk == _FakePool.instances[:5], "eviction must be oldest-first"
    assert not any(p.shrunk for p in _FakePool.instances[5:]), "hot pools untouched"
    # a NEW conninfo evicts exactly one more (shrink, never close)
    m._get_pool("dbname=fake host=/tmp/pc21")
    assert len([p for p in _FakePool.instances if p.shrunk]) == 6


def test_eviction_never_drops_hot_entry(monkeypatch, tmp_path):
    m = _load_pgconn(monkeypatch, tmp_path)
    m._pools.clear()
    hot = m._get_pool("dbname=hot host=/tmp/hot")
    for i in range(3):
        m._get_pool(f"dbname=cold host=/tmp/pc{i}")
    assert m._get_pool("dbname=hot host=/tmp/hot") is hot
    assert len(m._pools) <= m._MAX_POOLS
