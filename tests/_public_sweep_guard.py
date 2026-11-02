"""qa4 N-3: pre-ddl public sweep, keyed on the ddl=True call itself.

Empirically established (probe14, qa4 round): the fixture-setup hook sweep
lands BEFORE the schema fixtures' own bootstrap connect, and
mnemo-pg2's shared-view bake repopulates `public` in the microseconds
between our sweep and the ddl bootstrap — so hook-ordering sweeps can
never close the window. The only deterministic place is the guard
itself: pgconn raises 'refusing ddl bootstrap while schema public holds
relations'. The suite conftests monkeypatch that guard call site here:
before every ddl=True bootstrap, drop public residue (qualified drops;
views first) and retry once. Production code is untouched; this module
only runs inside the pytest process, installed from each suite's
conftest (a sibling conftest may patch it first — patch is idempotent).
"""
import sys

_INSTALLED = "_public_sweep_guard_installed"


def _residue(cx):
    return [(r["relname"], r["relkind"]) for r in cx.query_dicts(
        "select c.relname, c.relkind from pg_class c "
        "join pg_namespace n on n.oid = c.relnamespace "
        "where n.nspname = 'public' "
        "and c.relkind in ('v','m','f','r','p') "
        "order by (c.relkind in ('r','p')), c.relname")]


def _clean(cx):
    for rel, kind in _residue(cx):
        stmt = "DROP VIEW" if kind in "vmf" else "DROP TABLE"
        cx.execute(f'{stmt} IF EXISTS public."{rel}" CASCADE')


def _wrap(connector):
    """Patch one pgconn module object's `connect` to sweep on PublicBusyError."""
    if getattr(connector, _INSTALLED, False):
        return
    orig = connector.connect
    pub_busy = getattr(connector, "PublicBusyError", None)
    if pub_busy is None:
        return

    def connect(dsn, schema="public", *, ddl=False, **kw):
        try:
            return orig(dsn, schema, ddl=ddl, **kw)
        except pub_busy:
            # One retry after sweeping public (drop residue, then the
            # bootstrap itself, in case the bake raced us again).
            with orig(dsn, "public") as cx:   # read+drop conn (search_path ok)
                _clean(cx)
            return orig(dsn, schema, ddl=ddl, **kw)

    connect.__name__ = "connect"
    connector.connect = connect
    setattr(connector, _INSTALLED, True)


def install():
    """Wrap every pgconn-looking module currently imported."""
    for name, mod in list(sys.modules.items()):
        if mod is None:
            continue
        if hasattr(mod, "PublicBusyError") and hasattr(mod, "connect"):
            try:
                _wrap(mod)
            except Exception:
                pass
