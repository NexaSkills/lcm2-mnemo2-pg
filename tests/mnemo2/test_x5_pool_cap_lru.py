"""Bounded pool cache — MNEMO-side parity contract (2026-11-09 hunt).

The runtime semantics (LRU touch, deterministic close, held-Conn
self-heal) are proven live by tests/lcm2/test_x5_pool_cap_lru.py; the
mnemo side mirrors them via SOURCE CONTRACT, not by exec'ing mnemo's
pgconn at test time: that import bootstraps the sibling suite's
envbind/impl machinery OUTSIDE the bare-arbiter's window and poisoned
later lcm2 `import engine` resolutions (r7 setup errors
'_mnemo2_impl_engine has no LCM2Engine' — caught the same day; the
machinery may only wake inside its own suite's window).
"""
import os

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SRC = os.path.join(REPO, "plugins", "mnemo-pg2", "pgconn.py")


def _src():
    with open(SRC, encoding="utf-8") as fh:
        return fh.read()


def test_pool_cache_is_bounded_with_lru():
    s = _src()
    assert "_MAX_POOLS" in s, "mnemo pgconn lost the pool cap"
    assert "while len(_pools) > _MAX_POOLS" in s, "eviction loop missing"
    assert "_pools.pop(conninfo)" in s, "LRU touch (pop+reinsert) missing"
    assert ".close(timeout=0)" in s, \
        "eviction must CLOSE deterministically (scheduler-starvation cure)"


def test_held_conn_self_heal_present():
    s = _src()
    assert "except PoolClosed:" in s, \
        "Conn.__enter__ must rebuild the pool after eviction-close"
