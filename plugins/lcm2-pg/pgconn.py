"""lcm2-pg PG connection layer. Pure psycopg (v3) — zero embedded-DB code (R3)."""
from __future__ import annotations

import atexit
import os
import re
import sys
import threading

# X-4 fail-close (must precede the psycopg imports): a missing psycopg
# raises a NAMED ImportError right here instead of feeding the plugin
# loader's 10s 'load timed out' lane (trade X-4 class). File-anchored:
# never resolves another plugin's copy by bare key.
def _x4_depcheck():
    import importlib.util as _u
    _f = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                      "_depcheck.py")
    _s = _u.spec_from_file_location("_lcm2_pg_depcheck_x4", _f)
    if _s is None or _s.loader is None:  # pragma: no cover
        return  # shim unreadable: the psycopg import below names itself
    _m = _u.module_from_spec(_s)
    _s.loader.exec_module(_m)
    _m.require_psycopg()


_x4_depcheck()

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

_pools: dict[tuple, ConnectionPool] = {}
_pools_lock = threading.Lock()
_ext_ready: set[str] = set()

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
    """Schema derived per profile: lcm2_<basename(LIVE HERMES_HOME)> (never
    pinned). B2 leak-fix: the home comes from hermes_constants.get_hermes_home
    (context-local override -> HERMES_HOME env -> platform default) so a
    multiplexed gateway's routed turn — scoped by CONTEXTVAR, os.environ
    untouched (gateway/run.py _profile_runtime_scope) — resolves to ITS OWN
    profile instead of collapsing every route into lcm2_default."""
    s = explicit or os.environ.get("LCM2_AGENT_SCHEMA")
    if not s:
        home = ""
        pname = None
        try:
            from hermes_constants import get_hermes_home

            h = get_hermes_home()
            if h:
                home = str(h)
        except Exception:  # no Hermes on this interpreter: env behaviour
            home = os.environ.get("HERMES_HOME", "")
        try:
            # W3TRADESCHEMA-MIKE-0030: name the bank by PROFILE ID when the
            # host can say which profile owns this home (root home ->
            # 'default', <root>/profiles/<name> -> the name). Without this
            # the '-p default' CLI lane (main.py early-return leaves
            # HERMES_HOME at the root home) sanitized basename '.hermes' to
            # '_hermes' and wrote the default lane into lcm2__hermes while
            # every gate measured lcm2_default. basename stays the
            # fallback for interpreters without Hermes / exotic homes.
            from hermes_constants import profile_name_for_home

            pname = profile_name_for_home(home) if home else None
        except Exception:  # pragma: no cover - no Hermes funnel
            pname = None
        if pname is None and home:
            # funnel absent (or an exotic home it cannot name): the
            # basename fallback maps the canonical root home '.hermes' to
            # 'default' directly — the mnemo side already carries this map
            # (_profile_name), so BOTH routes name the default lane
            # lcm2_default. Sanitizing '.hermes' to '_hermes' was the bug.
            bn = os.path.basename(home.rstrip("/"))
            if bn == ".hermes":
                pname = "default"
        base = pname or (os.path.basename(home.rstrip("/"))
                         if home else "default")
        s = f"lcm2_{re.sub(r'[^a-z0-9_]', '_', base.lower())}"
    if not _SCHEMA_RE.match(s):
        raise ValueError(f"invalid schema name: {s!r}")
    return s


def _ensure_ext_in_pg_catalog(conninfo: str) -> None:
    """Best-effort, once per DSN: move vector/pg_trgm into pg_catalog so a
    public-free search_path still resolves extension types and operator
    classes (pg_catalog is implicitly appended to every search_path).

    Requires superuser (this instance's role is). If it fails we proceed —
    visible warning, never a silent half-state."""
    if conninfo in _ext_ready:
        return
    try:
        with psycopg.connect(conninfo, autocommit=True) as cx:
            for ext in ("vector", "pg_trgm"):
                row = cx.execute(
                    "select n.nspname from pg_extension e join pg_namespace n"
                    " on n.oid = e.extnamespace where e.extname = %s",
                    (ext,)).fetchone()
                if row is None:
                    cx.execute(
                        f'CREATE EXTENSION IF NOT EXISTS "{ext}" '
                        "SCHEMA pg_catalog")
                elif row[0] != "pg_catalog":
                    cx.execute(
                        f'ALTER EXTENSION "{ext}" SET SCHEMA pg_catalog')
    except Exception as exc:  # visible, non-fatal
        import logging
        logging.getLogger("lcm2-pg").warning(
            "lcm2-pg: could not relocate extensions to pg_catalog (%s); "
            "continuing without", exc)
    _ext_ready.add(conninfo)


def _get_pool(conninfo: str) -> ConnectionPool:
    with _pools_lock:
        pool = _pools.get(conninfo)
        if pool is None:
            _ensure_ext_in_pg_catalog(conninfo)
            pool = ConnectionPool(
                conninfo, min_size=1, max_size=8, open=True, timeout=15.0,
                max_idle=30.0,  # recycle idle conns: never hold a stale lock
                kwargs={"row_factory": dict_row, "autocommit": True,
                        "prepare_threshold": None,  # no PREPARE: fork-safe (R8)
                        "application_name": "lcm2-pg"},
            )
            _pools[conninfo] = pool
        return pool


class Conn:
    """Thin wrapper: one checkout + search_path pin, per operation block."""

    def __init__(self, dsn: str, schema: str, ddl: bool = False,
                 set_session: bool = False):
        if not _SCHEMA_RE.match(schema):
            raise ValueError(f"invalid schema name: {schema!r}")
        self._pool = _get_pool(dsn)
        self._schema = schema
        self._ddl = ddl
        self._set_session = set_session
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
        # session-level (is_local=false) pin: re-applied automatically on
        # every pool reconnect/reset; a transaction-local set_config is lost
        # silently when the pool ends the implicit transaction at checkin.
        # NO public fallback: an unqualified name resolves ONLY inside this
        # agent's own schema. A ', public' tail let unqualified SQL hit
        # residue tables in public (the exact R8-flake public-shadowing
        # failure mode). bootstrap()/DDL is fully schema-qualified and the
        # pg_catalog implicit search_path entry keeps extension types
        # (vector, now(), gen_random_uuid) resolvable, so strictness costs
        # nothing.
        # DELIBERATE: ddl=True (bootstrap) connects under schema `public`
        # (bootstrap DDL is unqualified — it must land in the session
        # schema; see test_r4_no_embedded). If public still holds relations
        # at that moment, bootstrap would silently build the whole 37-table
        # store THERE (the F3 public-residue regeneration loop). Fail closed
        # instead: public must be empty (scripts/sweep_public_residue.py);
        # extensions resolve via pg_catalog (_ensure_ext_in_pg_catalog).
        if self._ddl and self._schema == "public" and self._public_busy():
            raise RuntimeError(
                "lcm2-pg: refusing ddl bootstrap while schema public holds "
                "relations (unqualified CREATE TABLE IF NOT EXISTS would "
                "land there). Run scripts/sweep_public_residue.py.")
        self._conn.execute(
            "select set_config('search_path', %s, false)", (self._schema,))
        return self

    def _public_busy(self) -> bool:
        return bool(self.query_scalar(
            "select exists(select 1 from pg_class c "
            "join pg_namespace n on n.oid = c.relnamespace "
            "where n.nspname = 'public' "
            "and c.relkind in ('r','p','m','v','f'))"))

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
    def _own_ddl(self):
        """Always our OWN ddl module, resolved by file location.

        'ddl' is a collision-prone top-level name: another suite on the
        same interpreter (mnemo-pg2 tests) can leave a different ddl module
        in sys.modules, and a plain `import ddl` would silently use it.

        Registered under a NAMESPACED name only (never bare 'ddl'): this
        runs inside plugin register() while OTHER threads (the host CLI)
        import concurrently — a bare-name publish would race the host's
        own imports of these names and can strand them as our flat
        modules ('tools' is not a package boot crash). tools.py reaches
        SCHEMA_VERSION via _selfimport.anchor('ddl'), not a bare import.
        """
        import importlib.util
        import pathlib
        path = pathlib.Path(__file__).resolve().parent / "ddl.py"
        mod = sys.modules.get("ddl")
        if mod is not None and getattr(mod, "__file__", None) and \
                pathlib.Path(mod.__file__).resolve() == path:
            return mod
        full = "_lcm2_pg_ddl"
        mod = sys.modules.get(full)
        if mod is not None and getattr(mod, "__file__", None) and \
                pathlib.Path(mod.__file__).resolve() == path:
            return mod
        spec = importlib.util.spec_from_file_location(full, path)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[full] = mod
        try:
            spec.loader.exec_module(mod)
        except BaseException:
            sys.modules.pop(full, None)
            raise
        return mod

    def bootstrap(self) -> None:
        self._own_ddl().apply_schema(self)


def connect(dsn: str | None, schema: str | None = None, ddl: bool = False,
            set_session: bool = False) -> Conn:
    return Conn(dsn or default_dsn(), schema or agent_schema(), ddl=ddl,
                set_session=set_session)


def shutdown_pools():
    with _pools_lock:
        for p in _pools.values():
            try:
                p.close()
            except Exception:
                pass
        _pools.clear()


# F-4: close every cached pool at interpreter exit. Without this, pool
# objects die during Python finalisation and psycopg_pool's __del__ tries
# to join its reaper threads -> stderr "PythonFinalizationError: cannot
# join thread at interpreter shutdown" noise on otherwise-clean runs.
# atexit runs in normal shutdown (before finalisation), where close is
# clean and silent. Child processes (spawn/fork) import this module fresh
# and create their own pools, so each registers its own hook; an
# os._exit() child never runs atexit and keeps the old __del__ noise —
# accepted, spawn children are transient.
atexit.register(shutdown_pools)
