"""mnemo-pg2 PG connection layer. Pure psycopg (v3) — zero embedded-DB code (R3).

DSN/config resolution mirrors lcm2-pg pgconn (plan §4):
  MNEMO2_PG_DSN (profile .env wins via host env load) >
  compose MNEMO2_PG_HOST/PORT/USER > dbname=mnemo2db fallback.
Private schema: MNEMO2_SCHEMA > deterministic mnemo2_<agent> from HERMES_HOME.
Shared schema: MNEMO2_SHARED_SCHEMA (default `shared`).

P3L: every os.environ read below routes through envbind.eff() — the
ambient env layered by the thread-local dispatch/pin override (a
register()-time binding from plugin_entry, or a full-env test pin). The
process-global env is never trusted lazily at connect time, so a rival
agent's env churn can never redirect THIS agent's connection. No
override installed (plain CLI / host single-agent use) => behaviour is
byte-identical to the old direct-env code.
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

try:  # sibling import inside OUR body: _SelfImportFinder serves OUR file
    import envbind as _envbind
except ModuleNotFoundError:  # direct-by-path load (tests/probes)
    import importlib.util as _ilu
    _p = _ilu.spec_from_file_location(
        "mnemo2_envbind_here", os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "envbind.py"))
    if _p is None or _p.loader is None:  # pragma: no cover
        raise ImportError("mnemo-pg2: envbind.py missing next to pgconn.py")
    _envbind = _ilu.module_from_spec(_p)
    _p.loader.exec_module(_envbind)

_pools: dict[tuple, ConnectionPool] = {}
_pools_lock = threading.Lock()
_ext_ready: set[str] = set()

_SCHEMA_RE = re.compile(r"^[a-z_][a-z0-9_]*$")


def _envget(name, default=None):
    ov = getattr(_envbind._tls, "env", None)
    if ov is None:
        return os.environ.get(name, default)
    if name in ov:
        return ov[name]
    return os.environ.get(name, default)


def default_dsn() -> str:
    dsn = _envget("MNEMO2_PG_DSN")
    if dsn:
        return dsn
    host = _envget("MNEMO2_PG_HOST",
                   os.path.expanduser("~/lcm2-mnemo2-pg/pg/run"))
    port = _envget("MNEMO2_PG_PORT", "54331")
    user = _envget("MNEMO2_PG_USER", "mnemo201")
    return f"dbname=mnemo2db host={host} port={port} user={user}"


def _profile_name() -> str:
    """Deterministic agent name: HERMES_HOME profile dir, else 'default'.
    NEVER a pinned profile list (fleet practice, same as lcm2-pg)."""
    hh = _envget("HERMES_HOME", "")
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
    s = explicit or _envget("MNEMO2_SCHEMA")
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
        m = re.search(r"dbname=(\w+)", self.dsn)
        self.dbname = m.group(1) if m else "postgres"
        self.schema = schema or agent_schema()
        if not _SCHEMA_RE.match(self.schema):
            raise ValueError(f"invalid schema name {self.schema!r}")
        self.kind = kind
        self._ddl_mod = ddl_mod
        self._want_ddl = ddl
        # cross-agent view re-bake flag (set true when DDL is wanted;
        # consumed once per connection in __enter__, same lifecycle as
        # _want_ddl — r8: the flag must always exist).
        self._want_views = bool(ddl)
        self._pool = _get_pool(self.dsn)
        self._cx = None
        self._public_busy = False

    def __enter__(self) -> "Conn":
        self._cx = self._pool.getconn()
        # DDL CRITICAL (P3X root cause): getconn() hands back a POOLED
        # connection with whatever session state the previous borrower
        # left. The runtime RESET/SETs are COMMITTED (idle-in-transaction
        # ends at the SET), so a rival bootstrap's pg_catalog-lead path
        # or another schema's path can be live on this connection before
        # we ever run. _ensure_clean resets per session-param, and PG
        # RESET search_path restores the ROLE default (empty here) —
        # useless. When this Conn will run DDL, reset-and-re-assert the
        # path immediately after getconn: pg_catalog-lead so even the
        # advisory lock probe can never resolve a relation through a
        # half-built FOREIGN schema (the pg_type_typname_nsp_index race).
        # Non-DDL conns keep the plain SET below.
        if self._want_ddl:
            self._cx.execute("SET search_path TO pg_catalog,pg_temp")
        self._schema_on_conn = self.schema
        self._conn_env_shared = os.environ.get("MNEMO2_SHARED_SCHEMA")
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
            # CREATE-time name resolution note: `CREATE VIEW x` lands in
            # the FIRST existing schema of search_path — a literal
            # `pg_catalog,` prefix would make unqualified view CREATEs
            # fail ("permission denied to create pg_catalog.x", observed).
            # So: our schema stays FIRST for CREATEs (correct target by
            # construction), and _own_ddl sets pg_catalog FIRST for just
            # the CREATE TABLE IF NOT EXISTS batch (catalogue probes must
            # never resolve through a half-built fresh schema — the
            # second pg_type race class). The trailing pg_catalog here is
            # only a safety net for the ordinary runtime path (always
            # exists implicitly; explicit for clarity, harmless).
            # DDL CRITICAL (P3X root cause): this SET runs BEFORE the
            # advisory lock and is COMMITTED (psycopg autocommit=False
            # does not imply an open txn — idle-in-transaction means no
            # open txn here). A pooled connection returned by another
            # Conn keeps whatever search_path THAT Conn set (including
            # _own_ddl's pg_catalog-lead for its CREATE batch, restored
            # only on that Conn's exit path). A second bootstrap thread
            # then getconn()s it and runs its CREATE TABLE IF NOT EXISTS
            # probes through the FOREIGN schema at the head of the path,
            # "finds" the rival's working_memory, skips, misses, and both
            # INSERT into pg_type -> pg_type_typname_nsp_index (the r8
            # race). Force OUR path INSIDE the lock, after it is held.
            self._cx.execute("SET search_path TO %s%s,pg_catalog"
                             % (self.schema, tail))
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
            # Lock is INSTANCE-GLOBAL (hashed on dsn only): pg_type,
            # pg_depend and pg_extension are database-global — concurrent
            # CREATE TABLE IF NOT EXISTS in DIFFERENT schemas still race
            # on pg_type_typname_nsp_index (the observed failure). Boot-
            # straps are IF-NOT-EXISTS-idempotent, so one global serial
            # lock is exactly what's needed; classid discriminator kept
            # for schema-level diagnostics only.
            try:
                # lockid2 = a CONSTANT, not max(classid): pg_class's max
                # oid differs per-database (template1 vs mnemo2db), and
                # advisory locks are INSTANCE-wide (shared memory) — a
                # per-db second key gave each database its own lock
                # space, so two same-DSN sessions in different databases
                # bootstrapped CONCURRENTLY onto the global pg_type.
                # CRITICAL (P3X close-out): this instance (PG 18.6) has
                # NO pg_advisory_xact_lock(bigint,bigint) overload (only
                # (bigint) and (int,int) — verified live on mnemo2db:
                # pg_proc lists 1-arg bigint and 2-arg int forms only).
                # The two-::bigint call below raised UndefinedFunction,
                # the except swallowed it, and NO lock was ever taken —
                # the 7f88ee2/92d138d serialization was a silent no-op
                # (the true root cause of the r8 DDL race). Single-key
                # overload, key = hash(dsn || 0x1f || dbname).
                self._cx.execute(
                    "SELECT pg_catalog.pg_advisory_xact_lock("
                    "pg_catalog.hashtext(%s || x'1f'::text || %s)::bigint)",
                    (self.dsn, self.dbname))
                # RE-ASSERT our path now that the lock is held: a pooled
                # conn may arrive with a FOREIGN search_path left by the
                # previous borrower's _own_ddl (pg_catalog-lead or another
                # schema). Inside the lock, this SET + everything _own_ddl
                # does is serialized against every other bootstrap.
                self._cx.execute("SET search_path TO pg_catalog,%s%s"
                                 % (self.schema, tail))
            except Exception:
                self._cx.rollback()
            self._own_ddl()
            self._want_ddl = False
        # _want_views: sibling-lane WIP flag (never set in __init__ at
        # 11b6845 — private view re-bake is dead code; statements()
        # already bakes the env-resolved views inside the lock). Fixed
        # to False in the close-out; the block returns to life only if
        # the lane sets it.
        if self.kind == "private" and getattr(self, "_want_views", False):
            # Cross-agent views: ensure them against the CONFIGURED
            # shared schema at connect time (bootstrap() only bakes
            # the default); invalid name = fail-closed, never silent.
            # Bind OUR ddl by FILE (namespaced key) — a bare `import
            # ddl` binds the HOST's package post-register-purge.
            #
            # R8 (P3R): the bake is NOT part of the bootstrap critical
            # path anymore. It used to run in a SEPARATE committed txn
            # outside the advisory lock — CREATE OR REPLACE VIEW holds
            # ACCESS EXCLUSIVE on the base tables in the OTHER (shared)
            # schema, serializing against that schema's own bootstrap
            # INSERTs into pg_type: the reader times out, retries, and
            # its IF-NOT-EXISTS catcache skip misses -> pg_type_typname
            # _nsp_index UniqueViolation (the observed surface-write
            # race). Instead: (1) _own_ddl already baked the env-resolved
            # views INSIDE the lock; (2) here we only RE-BAKE when the
            # connection's shared schema differs from the env-resolved
            # one, and we take the SAME advisory lock first, so a
            # concurrent bootstrap can never interleave.
            _d = sys.modules.get("_mnemo2_impl_ddl")
            if _d is None or getattr(_d, "shared_view_statements", None) is None:
                self._own_ddl()  # binds _mnemo2_impl_ddl by file
                _d = sys.modules["_mnemo2_impl_ddl"]
            # baked by _own_ddl under the value of MNEMO2_SHARED_SCHEMA
            # AT CONNECT TIME (env is global and threads rewrite it —
            # read it at __enter__, never here). Re-bake only when the
            # bake's target differs from what we want NOW.
            env_shared = getattr(self, "_conn_env_shared", None)
            want = shared_schema()
            try:
                if want != env_shared:
                    # different shared target than _own_ddl baked: bake
                    # OUR views under the lock too
                    # CRITICAL (P3X close-out): this instance (PG 18.6) has
                    # NO pg_advisory_xact_lock(bigint,bigint) overload (only
                    # (bigint) and (int,int) — verified on mnemo2db). The
                    # two-::bigint form raised UndefinedFunction, the except
                    # swallowed it, and NO lock was ever taken — the whole
                    # 7f88ee2/92d138d serialization was a silent no-op. Use
                    # the single-bigint overload, key = hash of dsn+dbname.
                    self._cx.execute(
                        "SELECT pg_catalog.pg_advisory_xact_lock("
                        "pg_catalog.hashtext(%s || x'1f'::text || %s)::bigint)",
                        (self.dsn, self.dbname))
                    for stmt in _d.shared_view_statements(want):
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
            # Late ddl binding: the register() purge deletes the bare
            # '_selfimport'/'ddl' keys, so the old `from _selfimport
            # import anchor` died on host-dispatched calls (F-2). Bind by
            # FILE next to this module, under namespaced keys only — zero
            # bare-name publication, works from any thread post-purge.
            import importlib.util as _ilu
            import os as _os
            _here = _os.path.dirname(_os.path.abspath(__file__))
            _ss = sys.modules.get("_mnemo2_impl_ddl")
            if _ss is not None and getattr(_ss, "statements", None) is not None:
                mod = _ss
            else:
                _p = _ilu.spec_from_file_location(
                    "_mnemo2_impl_ddl", _os.path.join(_here, "ddl.py"))
                if _p is None or _p.loader is None:  # pragma: no cover
                    raise ImportError("mnemo-pg2: ddl.py missing next to pgconn.py")
                mod = _ilu.module_from_spec(_p)
                sys.modules["_mnemo2_impl_ddl"] = mod
                _p.loader.exec_module(mod)
        # pgvector must exist in THIS database before any VECTOR-typed DDL.
        # CRITICAL (P3PIN root cause): the commit below ENDS the bootstrap
        # transaction, which silently RELEASES the xact-scoped advisory
        # lock __enter__ took — every later CREATE TABLE IF NOT EXISTS in
        # this bootstrap runs UNLOCKED (the server observer proved it:
        # zero advisory rows in pg_locks while two shared-kind backends
        # were both mid-CREATE-TABLE). A mid-lock commit reintroduces the
        # pg_type_typname_nsp_index window. So: probe first (catalog read,
        # lock still held); only when the extension is genuinely MISSING
        # run the commit-isolated CREATE (it must stay commit-isolated:
        # an open CREATE EXTENSION in the same txn as the table batch
        # trips pg_depend races). When it already exists — the normal
        # bootstrap case — there is NO commit and the lock is held
        # unbroken through the whole CREATE TABLE batch.
        try:
            _have = self.query_scalar(
                "select count(*) from pg_catalog.pg_extension "
                "where extname = 'vector'")
            if not _have:
                self._cx.execute("CREATE EXTENSION IF NOT EXISTS vector")
                self._cx.commit()
                # the commit dropped the xact lock: re-acquire (same key,
                # same semantics as __enter__) before the table batch.
                self._cx.execute(
                    "SELECT pg_catalog.pg_advisory_xact_lock("
                    "pg_catalog.hashtext(%s || x'1f'::text || %s)::bigint)",
                    (self.dsn, self.dbname))
        except Exception:
            self._cx.rollback()
        # Pass OUR resolved schema in (kind-true): ddl.py's env fallback
        # would re-derive mnemo2_<profile> for a private Conn even when
        # this Conn points at a test/scratch schema (kind=shared reads
        # MNEMO2_SHARED_SCHEMA instead of Conn.schema).
        #
        # R8 (P3R) second pg_type race class: while CREATE TABLE IF NOT
        # EXISTS runs, a FRESH (not-yet-bootstrapped) schema resolves
        # even the catalogue helper probes through itself, so two
        # bootstraps in different fresh schemas can each "find" the
        # other's working_memory, skip, then both insert ->
        # pg_type_typname_nsp_index UniqueViolation. Pin pg_catalog
        # FIRST for the CREATE TABLE batch only (name resolution for
        # unqualified CREATEs in this batch is schema-qualified by
        # ddl.statements anyway). Restored afterwards so view CREATEs
        # still land in a valid schema (pg_catalog is not creatable).
        stmts = mod.statements(self.kind, self.schema)
        # statements(kind=private) appends the CROSS-AGENT views (bare
        # target names, pointing at the shared schema). Under the
        # pg_catalog-pinned table batch a bare CREATE VIEW would target
        # pg_catalog -> fail. Use the qualified bake form for those.
        if self.kind == "private":
            stmts = [s for s in stmts if "CREATE OR REPLACE VIEW shared_"
                     not in s] + list(
                mod.bake_view_statements(self.schema))
        _in_tables = False
        for stmt in stmts:
            if stmt.startswith("CREATE TABLE"):
                if not _in_tables:
                    self._cx.execute(
                        "SET search_path TO pg_catalog,%s" % self.schema)
                    _in_tables = True
            elif _in_tables:
                self._cx.execute(
                    "SET search_path TO %s%s,pg_catalog"
                    % (self.schema, "" if self.kind == "shared" else ", public"))
                _in_tables = False
            self._cx.execute(stmt)
        if _in_tables:
            self._cx.execute(
                "SET search_path TO %s%s,pg_catalog"
                % (self.schema, "" if self.kind == "shared" else ", public"))
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
