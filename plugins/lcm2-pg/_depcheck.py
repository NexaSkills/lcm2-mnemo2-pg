"""X-4 fail-close shim: psycopg (v3) missing in THIS interpreter.

Signature this cures (trade, debbie rebattery-close REPO-WATCH): plugin
`__file__` resolves into the repo and the host logs "Failed to load
plugin 'mnemo-pg2': load timed out after 10s (import + register() never
returned)" x216/day while the plugin silently 0-rows. Mechanism: the
plugin's load runs on a worker thread
(hermes_cli/plugins_loader._load_with_timeout); a top-level ImportError
raised there is captured and re-raised on the loader thread, but the
import-lane shadowing dance in these packages makes that path
non-deterministic — the observed failure mode is the worker *stuck*, so
the loud exception never surfaces. A missing dependency must fail FAST
and NAME IT on every route instead of racing a 10s timeout.

This module ships in BOTH plugin dirs (mnemo-pg2 and lcm2-pg) and is
imported as the FIRST executable line of each package's pgconn.py,
before `import psycopg`. It does NOT create a psycopg stand-in: it
raises ImportError whose message names the missing dependency, the
interpreter, the install command, and the two plugin names — the fail-
close contract (R3: no silent fallback, ever).

Token MIKE-X10-0432
"""
from __future__ import annotations

import sys

_MISSING = (
    "lcm2-pg/mnemo-pg2 require psycopg (v3, psycopg[binary] or "
    "psycopg[c]) in the interpreter that loads them. This interpreter "
    "has none: {exe}. Install it into THAT interpreter's environment, "
    "e.g.:  {exe} -m pip install 'psycopg[binary]' psycopg-pool. "
    "NOTE: the hermes launcher wrapper runs 'python -I' (isolated mode) "
    "and system python3 are separate environments from any venv — "
    "install where the gateway/CLI process actually runs (check the "
    "failing process, not `which python3`). A missing psycopg here must "
    "fail loud at import, NOT via the plugin loader's 10s "
    "'load timed out' lane (the trade X-4 class: 216 silent timeouts, "
    "0 PG rows)."
)


def require_psycopg() -> None:
    """Raise a NAMED ImportError when psycopg is unimportable here.

    Checks sys.modules first (a live binding means the dependency is
    satisfied), then a cheap finders-only probe (no import side
    effects, no __import__ machinery running package code). Raises —
    never returns a stub."""
    if "psycopg" in sys.modules:
        return
    try:
        from importlib.util import find_spec
        if find_spec("psycopg") is not None:
            return
    except (ImportError, ValueError, ModuleNotFoundError):
        pass  # finders confused: fall through to the real attempt below
    try:
        import psycopg  # noqa: F401  (real attempt; success = satisfied)
        return
    except ImportError as exc:
        if exc.name is not None and exc.name != "psycopg" \
                and not exc.name.startswith("psycopg."):
            return  # psycopg IS present; something else failed deeper —
                    # pgconn's own import surfaces that with its own name
        raise ImportError(
            _MISSING.format(exe=getattr(sys, "executable", "?"))
        ) from exc
