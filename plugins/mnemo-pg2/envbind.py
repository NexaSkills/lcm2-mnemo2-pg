"""P3L: per-write schema binding — Engine binds dsn/schema/shared_schema at
REGISTER (dispatch-bind) time, not lazily from the process-global env at
every call.

Product problem (r8, P3L): the plugin's handlers built a fresh Engine()
per dispatch and resolved schema from os.environ AT THE CALL. Env is
process-global: two agents registered in one interpreter (host plugin
host + tests) rewrite MNEMO2_SCHEMA/MNEMO2_SHARED_SCHEMA around their
work, so a write whose resolution lands inside the rival's window goes
to the WRONG schema (proven: agent a's writes counted up to 96 times in
agent b's schema under amplified interleaving; no errors, silent
misplacement).

Fix: capture the resolved triple per Engine. Handlers dispatch through
the per-agent wrappers plugin_entry builds at register(), so the
capture point is register() — env read exactly once per agent, at bind
time, immune to later env churn. An Engine() built directly (host/other
routes) behaves exactly as before: per-call env resolution."""
import os
import threading

_tls = threading.local()


def _resolve(explicit, env_name, default_fn):
    """explicit > thread-local override > env (via default_fn)."""
    if explicit:
        return explicit
    ov = getattr(_tls, "env", None)
    if ov is not None and env_name in ov:
        return ov[env_name]
    return default_fn()


def bind_env(dsn=None, schema=None, shared_schema=None):
    """Set the thread-local env override consulted by _resolve (see
    scoped()). None-valued fields fall through to the ambient env."""
    _tls.env = {k: v for k, v in (("dsn", dsn), ("schema", schema),
                                  ("shared_schema", shared_schema))
                if v}
    return _tls.env


def scoped(dsn=None, schema=None, shared_schema=None):
    """Context manager: bind the thread-local resolution (restoring the
    previous binding on exit). Passing nothing just isolates THIS block's
    resolution from the ambient env without naming anything."""
    class _Scope:
        def __enter__(self_inner):
            self_inner.prev = getattr(_tls, "env", None)
            bind_env(dsn, schema, shared_schema)
            return self_inner

        def __exit__(self_inner, *exc):
            _tls.env = self_inner.prev
            return False
    return _Scope()
