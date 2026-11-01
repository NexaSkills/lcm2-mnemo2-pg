"""lcm2-pg PG connection layer. Pure psycopg (v3) — zero embedded-DB code (R3)."""
from __future__ import annotations

import os
import re
import threading

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

_pools: dict[tuple, ConnectionPool] = {}
_pools_lock = threading.Lock()

_SCHEMA_RE = re.compile(r"^[a-z_][a-z0-9_]*$")


def default_dsn() -> str:
    """Profile .env LCM2_PG_DSN wins; else compose from global parts."""
    dsn = os.environ.get("LCM2_PG_DSN")
    if dsn:
        return dsn
    host = os.environ.get("LCM2_PG_HOST",
                          os.path.expanduser("~/lcm2-mnemo2-pg/pg/run"))
    port = os.environ.get("LCM2_PG_PORT", "54331")
    user = os.environ.get("LCM2_PG_USER", "mnemo201")
    return f"dbname=lcm2db host={host} port={port} user={user}"


def agent_schema(explicit: str | None = None) -> str:
    """Schema derived per profile: lcm2_<basename(HERMES_HOME)> (never pinned)."""
    s = explicit or os.environ.get("LCM2_AGENT_SCHEMA")
    if not s:
        home = os.environ.get("HERMES_HOME", "")
        base = os.path.basename(home.rstrip("/")) if home else "default"
        s = f"lcm2_{re.sub(r'[^a-z0-9_]', '_', base.lower())}"
    if not _SCHEMA_RE.match(s):
        raise ValueError(f"invalid schema name: {s!r}")
    return s


def _get_pool(conninfo: str) -> ConnectionPool:
    with _pools_lock:
        pool = _pools.get(conninfo)
        if pool is None:
            pool = ConnectionPool(
                conninfo, min_size=1, max_size=8, open=True, timeout=15.0,
                max_idle=30.0,  # recycle idle conns: never hold a stale lock
                kwargs={"row_factory": dict_row, "autocommit": True,
                        "application_name": "lcm2-pg"},
            )
            _pools[conninfo] = pool
        return pool


class Conn:
    """Thin wrapper: one checkout + search_path pin, per operation block."""

    def __init__(self, dsn: str, schema: str, ddl: bool = False):
        if not _SCHEMA_RE.match(schema):
            raise ValueError(f"invalid schema name: {schema!r}")
        self._pool = _get_pool(dsn)
        self._schema = schema
        self._ddl = ddl
        self._cm = None
        self._conn = None
        self.ddl_log: list[str] = []

    def __enter__(self) -> "Conn":
        try:
            self._cm = self._pool.connection()
            self._conn = self._cm.__enter__()
        except Exception:
            # checkout failed (server unreachable / pool timeout): the
            # context manager never ran, so close the pool we just opened
            # for this DSN. Without this a dead endpoint leaves its pool
            # cached with an abandoned waiter and every later checkout in
            # the process times out instead of raising a clean error.
            self._evict_pool()
            raise
        self._conn.execute(
            "select set_config('search_path', %s || ', public', false)",
            (self._schema,))
        return self

    def _evict_pool(self) -> None:
        """Close+drop THIS Conn's pool (used when checkout never succeeded)."""
        with _pools_lock:
            for key, p in list(_pools.items()):
                if p is self._pool:
                    _pools.pop(key, None)
                    break
        try:
            self._pool.close()
        except Exception:
            pass

    def __exit__(self, *exc):
        rc = self._cm.__exit__(*exc)
        self._conn = None
        return rc

    # -- query helpers (parameterized SQL only) -------------------------
    def execute(self, sql: str, params=None):
        cur = self._conn.execute(sql, params)
        if self._ddl:
            self.ddl_log.append(sql.strip())
        return cur

    def query(self, sql: str, params=None) -> list[tuple]:
        cur = self._conn.execute(sql, params)
        return [tuple(r.values()) for r in cur.fetchall()]

    def query_dicts(self, sql: str, params=None) -> list[dict]:
        return list(self._conn.execute(sql, params).fetchall())

    def query_scalar(self, sql: str, params=None):
        cur = self._conn.execute(sql, params)
        row = cur.fetchone()
        return next(iter(row.values())) if row else None

    # -- bootstrap ------------------------------------------------------
    def bootstrap(self) -> None:
        # import here as a module object (patch-friendly, no name binding)
        import ddl as _ddl
        _ddl.apply_schema(self)


def connect(dsn: str | None, schema: str | None = None, ddl: bool = False) -> Conn:
    return Conn(dsn or default_dsn(), schema or agent_schema(), ddl=ddl)


def shutdown_pools():
    with _pools_lock:
        for p in _pools.values():
            try:
                p.close()
            except Exception:
                pass
        _pools.clear()
