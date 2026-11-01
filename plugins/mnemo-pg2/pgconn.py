"""mnemo-pg2 PG connection layer. Pure psycopg (v3) — zero embedded-DB code (R3).

DSN/config resolution mirrors lcm2-pg pgconn (plan §4):
  MNEMO2_PG_DSN (profile .env wins via host env load) >
  compose MNEMO2_PG_HOST/PORT/USER > dbname=mnemo2db fallback.
Private schema: MNEMO2_SCHEMA > deterministic mnemo2_<agent> from HERMES_HOME.
Shared schema: MNEMO2_SHARED_SCHEMA (default `shared`).
"""
from __future__ import annotations

import atexit
import os
import re
import sys
import threading

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

_pools: dict[tuple, ConnectionPool] = {}
_pools_lock = threading.Lock()
_ext_ready: set[str] = set()

_SCHEMA_RE = re.compile(r"^[a-z_][a-z0-9_]*$")


def default_dsn() -> str:
    dsn = os.environ.get("MNEMO2_PG_DSN")
    if dsn:
        return dsn
    host = os.environ.get("MNEMO2_PG_HOST",
                          os.path.expanduser("~/lcm2-mnemo2-pg/pg/run"))
    port = os.environ.get("MNEMO2_PG_PORT", "54331")
    user = os.environ.get("MNEMO2_PG_USER", "mnemo201")
    return f"dbname=mnemo2db host={host} port={port} user={user}"


def _profile_name() -> str:
    """Deterministic agent name: HERMES_HOME profile dir, else 'default'.
    NEVER a pinned profile list (fleet practice, same as lcm2-pg)."""
    hh = os.environ.get("HERMES_HOME", "")
    if hh:
        base = os.path.basename(hh.rstrip("/"))
        if base.startswith("profiles/"):  # pragma: no cover
            base = hh.rstrip("/").split("/")[-1]
        if base and base != ".hermes":
            return base
        if base == ".hermes":
            return "default"
    return "default"


def agent_schema(explicit: str | None = None) -> str:
    s = explicit or os.environ.get("MNEMO2_SCHEMA")
    if not s:
        s = "mnemo2_" + re.sub(r"[^a-z0-9_]", "_", _profile_name().lower())
    if not _SCHEMA_RE.match(s):
        raise ValueError(f"invalid MNEMO2_SCHEMA {s!r}")
    return s


def shared_schema() -> str:
    s = os.environ.get("MNEMO2_SHARED_SCHEMA", "shared")
    if not _SCHEMA_RE.match(s):
        raise ValueError(f"invalid MNEMO2_SHARED_SCHEMA {s!r}")
    return s


def _ensure_ext_in_pg_catalog(conninfo: str) -> None:
    """Best-effort, once per DSN (mirrors lcm2-pg proven pattern): ensure
    vector/pg_trgm exist IN THE CONNECTED DATABASE and live in pg_catalog so
    a public-free / strict search_path still resolves extension types
    (pg_catalog is implicitly appended to every search_path). Extension
    existence is per-database: probe the target dbname (parsed from the
    dsn) AND postgres, never assume template propagation."""
    if conninfo in _ext_ready:
        return
    m = __import__("re").search(r"dbname=(\w+)", conninfo)
    target = m.group(1) if m else "postgres"
    try:
        for db in {target, "postgres"}:
            with psycopg.connect(conninfo, dbname=db, autocommit=True) as cx:
                for ext in ("vector", "pg_trgm"):
                    row = cx.execute(
                        "select n.nspname from pg_extension e join "
                        "pg_namespace n on n.oid = e.extnamespace "
                        "where e.extname = %s", (ext,)).fetchone()
                    if row is None:
                        cx.execute(
                            f'CREATE EXTENSION IF NOT EXISTS "{ext}" '
                            "SCHEMA pg_catalog")
                    elif row[0] != "pg_catalog":
                        cx.execute(
                            f'ALTER EXTENSION "{ext}" SET SCHEMA pg_catalog')
    except Exception as exc:  # visible, non-fatal
        import logging
        logging.getLogger("mnemo-pg2").warning(
            "mnemo-pg2: could not prepare extensions in pg_catalog (%s); "
            "continuing without", exc)
    _ext_ready.add(conninfo)


def _get_pool(conninfo: str) -> ConnectionPool:
    with _pools_lock:
        pool = _pools.get(conninfo)
        if pool is None:
            _ensure_ext_in_pg_catalog(conninfo)
            pool = ConnectionPool(
                conninfo, min_size=1, max_size=8, open=True,
                timeout=10, max_idle=300,
                kwargs={"row_factory": dict_row, "autocommit": False},
            )
            _pools[conninfo] = pool
        return pool


class Conn:
    """One schema-scoped connection. `kind` selects the DDL shape:
    'private' (full mnemo2_<agent> table set) or 'shared' (surface subset)."""

    def __init__(self, dsn: str, schema: str, ddl: bool = False,
                 kind: str = "private", ddl_mod=None):
        self.dsn = dsn or default_dsn()
        self.schema = schema or agent_schema()
        if not _SCHEMA_RE.match(self.schema):
            raise ValueError(f"invalid schema name {self.schema!r}")
        self.kind = kind
        self._ddl_mod = ddl_mod
        self._want_ddl = ddl
        self._pool = _get_pool(self.dsn)
        self._cx = None
        self._public_busy = False

    def __enter__(self) -> "Conn":
        self._cx = self._pool.getconn()
        self._schema_on_conn = self.schema
        # OUR schema is bound session-locally ONLY (never SET ... LOCAL):
        # it is RESET in __exit__ before the connection returns to the
        # pool, so a shared-surface call can never read or write through
        # a stale private search_path (the public.shared_* views would
        # shadow the real shared schema). Fail-closed, not shadow-able.
        try:
            # SET rejects bind params; names validated in __init__ (%s on
            # identifiers only, never user data). Surface (shared-bank)
            # connections get an EMPTY public: cross-agent views are baked
            # fully-qualified into the shared schema itself (a
            # private-owned public.shared_* view must never shadow the
            # real shared tables — R5 fail-closed, not search-order).
            tail = "" if self.kind == "shared" else ", public"
            self._cx.execute("SET search_path TO %s%s" % (self.schema, tail))
            if not self._cx.autocommit:
                self._cx.commit()
        except Exception:
            self._cx.rollback()
            raise
        if self._want_ddl:
            # FIRST-WRITE-ER-WINS bootstrap (concurrency-safe, R8): every
            # connect wants DDL but only ONE backend per (dsn, schema) may
            # run the CREATEs — two concurrent CREATE TABLE IF NOT EXISTS
            # race on pg_type (pg_type_typname_nsp_index UniqueViolation).
            # The lock stays HELD to the end of the bootstrap transaction
            # (xact-scoped, no commit here — _own_ddl commits at the end),
            # which also serializes its CREATE EXTENSION against pg_depend.
            try:
                self._cx.execute(
                    "SELECT pg_advisory_xact_lock("
                    "hashtext(%s)::bigint, "
                    "(SELECT COALESCE(max(classid),0) FROM pg_class)::bigint)",
                    (f"{self.dsn}\x1f{self.schema}",))
            except Exception:
                self._cx.rollback()
            self._own_ddl()
            self._want_ddl = False
            if self.kind == "private":
                # Cross-agent views: ensure them against the CONFIGURED
                # shared schema at connect time (bootstrap() only bakes
                # the default); invalid name = fail-closed, never silent.
                import ddl as _ddl
                try:
                    for stmt in _ddl.shared_view_statements():
                        self._cx.execute(stmt)
                    self._cx.commit()
                except Exception:
                    self._cx.rollback()
        return self

    def __exit__(self, *exc):
        cx = self._cx
        self._cx = None
        if cx is None:
            return False
        try:
            if exc[0] is not None:
                cx.rollback()
            else:
                cx.commit()
        finally:
            try:
                cx.execute("RESET search_path")
                cx.commit()
            except Exception:
                try:
                    cx.rollback()
                except Exception:
                    pass
            self._pool.putconn(cx)
        return False

    # -- api ------------------------------------------------------------
    def execute(self, sql: str, params=None):
        cur = self._cx.execute(sql, params)
        self._cur = cur
        return cur

    def query(self, sql: str, params=None) -> list[tuple]:
        cur = self._cx.execute(sql, params)
        return [tuple(r.values()) for r in cur.fetchall()]

    def query_dicts(self, sql: str, params=None) -> list[dict]:
        cur = self._cx.execute(sql, params)
        return list(cur.fetchall())

    def query_scalar(self, sql: str, params=None):
        cur = self._cx.execute(sql, params)
        row = cur.fetchone()
        return None if row is None else next(iter(row.values()))

    # -- ddl --------------------------------------------------------------
    def _own_ddl(self):
        mod = self._ddl_mod
        if mod is None:
            from _selfimport import anchor  # noqa: plugin-local import
            mod = anchor("ddl")
        # pgvector must exist in THIS database before any VECTOR-typed DDL.
        # Serialize on the SAME advisory-lock key class as the table
        # CREATEs: CREATE EXTENSION races with concurrent CREATE TABLE on
        # pg_depend (FeatureNotSupported: cannot alter a subobject of the
        # extension). In one transaction with the lock (xact lock, not
        # commit — committing the lock would release it early).
        try:
            self._cx.execute(
                "SELECT pg_advisory_xact_lock("
                "hashtext(%s)::bigint, "
                "(SELECT COALESCE(max(classid),0) FROM pg_class)::bigint)",
                (f"{self.dsn}\x1f{self.schema}",))
            self._cx.execute("CREATE EXTENSION IF NOT EXISTS vector")
            self._cx.commit()
        except Exception:
            self._cx.rollback()
        for stmt in mod.statements(self.kind):
            self._cx.execute(stmt)
        if self.kind == "shared":
            # Cross-agent views INSIDE the shared schema, fully qualified
            # (self-pointing). Surface connections run with public pruned
            # from search_path (pgconn __enter__), so a private agent that
            # planted public.shared_* can never shadow these — R5
            # fail-closed by topology, not by search order.
            try:
                for stmt in mod.shared_view_statements(self.schema):
                    self._cx.execute(stmt)
                self._cx.commit()
            except Exception:
                self._cx.rollback()
        self._cx.commit()

    def bootstrap(self) -> None:
        self._want_ddl = True


def connect(dsn: str | None, schema: str | None = None, ddl: bool = False,
            kind: str = "private", ddl_mod=None) -> Conn:
    return Conn(dsn or default_dsn(), schema or agent_schema(), ddl=ddl,
                kind=kind, ddl_mod=ddl_mod)


def shutdown_pools():
    with _pools_lock:
        for name, pool in list(_pools.items()):
            try:
                pool.close()
            except Exception:
                pass
        _pools.clear()


atexit.register(shutdown_pools)
