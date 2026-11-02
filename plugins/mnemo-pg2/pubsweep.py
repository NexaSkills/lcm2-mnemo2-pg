"""Self-contained public-residue sweep for the shared lcm2 instance (qa4 N-3).

WHY this is plugin-owned (not conftest-owned): the residue's origin is
mnemo-pg2's OWN legacy-vendor compatibility bake — bootstrap() publishes
every public base table as a root-view (public.<name> AS SELECT * FROM
<agent_schema>.<name>, ddl._publish_public_views), and those views are
owned (auto) by rows in <agent_schema>.shared_registry. An agent schema
that was DROPped (a crashed/concurrent test teardown, a probe) leaves the
root-views Dangling orphans pointing at the vanished base tables — 121 of
them on mnemo2db at qa4. conftest sweeps cannot reach this class: the
orphan cleanup in ddl.reconcile_public_views runs only inside a successful
bootstrap's transaction, and the ddl bootstrap itself refuses to run while
public is busy — a deadlock in exactly the residue state. So every
mnemo-pg2 connect self-heals the state its own bake created.

Rules (same discipline as scripts/sweep_public_residue.py):
  * root-views whose base table is GONE (dangling/missing dependents):
    dropped, under lock, re-verified before the DROP;
  * root-views of LIVE base tables (real shared-registry cross-agent
    sharing): NEVER touched;
  * non-view relations (tables/matviews) in public: dropped UNCONDITIONALLY
    — agent data lives in agent schemas, never public (this is the class
    that trips the ddl public-busy guard in lcm2-pg's pgconn).
"""
from __future__ import annotations

import threading

_lock = threading.Lock()

_SQL_DANGLING = """
with v as (
  select c.oid, c.relname,
         (d.deptype = 'a') as auto,
         coalesce(bool_agg(dc.oid is not null
                        and dc.relnamespace = d.refobjnamespace
                        and dc.relname = d.refobjname
                        and dc.relkind in ('r','p','m','f')), false) as base_live
    from pg_class c
    join pg_namespace n on n.oid = c.relnamespace
    left join pg_depend d
           on d.objid = c.oid and d.classid = 'pg_class'::regclass
              and d.refclassid = 'pg_class'::regclass
          and d.refobjsubid = 0
    left join pg_class dc
           on dc.oid = d.refobjid and dc.relkind in ('r','p','m','f')
   where n.nspname = 'public' and c.relkind = 'v'
   group by c.oid, c.relname, auto, d.refobjname, d.refobjnamespace
)
select oid::regclass::text from v where not base_live
"""

# (oid::regclass text) of the dropped objects — for logging
def _droppable(cx):
    dangling = [r[0] for r in cx.query(
        "select oid::regclass::text from (" + _SQL_DANGLING + ") q")]
    tables = [r[0] for r in cx.query(
        "select c.relname from pg_class c "
        "join pg_namespace n on n.oid = c.relnamespace "
        "where n.nspname = 'public' "
        "and c.relkind in ('r','p','m','f') order by 1")]
    return dangling, tables


def sweep(conninfo: str) -> int:
    """Best-effort, idempotent. Returns number of objects dropped.
    Never raises: sweep failure must never break a connect() (worst case
    is the pre-sweep behaviour: the ddl guard's fail-closed error)."""
    import pgconn as _pg  # our module (latebind pins this file)
    n = 0
    try:
        with _pg.connect(conninfo, "public") as cx:
            dangling, tables = _droppable(cx)
            if not dangling and not tables:
                return 0
            # serialize against a concurrent bootstrap's own reconcile
            cx.execute("select pg_advisory_lock(hashtext('mnemo2_public_sweep'))")
            try:
                # re-verify under lock: a live bake between detect and lock
                # may have (re)attached bases — never drop a live root-view
                live_dangling, live_tables = _droppable(cx)
                for ref in live_dangling:
                    if ref in dangling:
                        cx.execute(f'DROP VIEW IF EXISTS {ref} CASCADE')
                        n += 1
                for rel in live_tables:
                    cx.execute(f'DROP TABLE IF EXISTS public."{rel}" CASCADE')
                    n += 1
            finally:
                cx.execute("select pg_advisory_unlock(hashtext('mnemo2_public_sweep'))")
    except Exception:
        pass
    return n
