"""lcm2-pg context engine — host-native resolution dir (F1).

The host's context-engine discovery (plugins/context_engine/__init__.py:
find_engine_dir) probes CHILD DIRECTORIES of $HERMES_HOME/plugins/ whose
__init__.py mentions ContextEngine — a symlink to the general plugin
package is never treated as an engine, so this dir exists to be the
resolution home for ``context.engine: lcm2-pg`` (profile layout:
$HERMES_HOME/plugins/context_engine/lcm2-pg).

NO BEHAVIOR LIVES HERE (one-line justification for this file's existence:
the discovery heuristic needs a dir; the implementation must stay single
in plugins/lcm2-pg/). The host's module scan runs BEFORE register() and
picks any ContextEngine subclass it finds in the module namespace, so we
expose exactly one: the bridge class from the general package
(plugins/lcm2-pg/context_engine_shim.py — a real
agent.context_engine.ContextEngine subclass that delegates every
protocol method verbatim to the plugin's LCM2Engine).

The package import inside _package() is file-anchored by absolute path
(never sys.path), because the discovery scan imports every child dir's
modules and cannot be relied on to have anchored the package first.
"""
from __future__ import annotations

import importlib.util as _ilu
import os as _os
import pathlib as _pathlib
import sys as _sys

# Marker read by the loader's dir heuristic (_is_context_engine_dir):
# ContextEngine / register_context_engine must appear in this file.
_ENGINE_MARKER = "ContextEngine"


def _package():
    """Import the general lcm2-pg plugin package by FILE (namespaced;
    publishes no bare names). Candidate order: env override, repo sibling
    (checkout layout), the profile/global plugin locations."""
    if "_lcm2_pg_ce_pkg" in _sys.modules:
        return _sys.modules["_lcm2_pg_ce_pkg"]
    here = _pathlib.Path(__file__).resolve().parent
    cands = [
        _os.environ.get("LCM2_PLUGIN_DIR"),
        str(here.parent.parent / "lcm2-pg"),   # checkout: plugins/context_engine/lcm2-pg -> sibling
        "~/lcm2-mnemo2-pg/plugins/lcm2-pg",
        "~/.hermes/plugins/lcm2-pg",
    ]
    for cand in cands:
        if not cand:
            continue
        init = _pathlib.Path(_os.path.expanduser(cand)) / "__init__.py"
        if init.is_file():
            spec = _ilu.spec_from_file_location(
                "_lcm2_pg_ce_pkg", init,
                submodule_search_locations=[str(init.parent)])
            if spec is None or spec.loader is None:  # pragma: no cover
                raise ImportError(f"lcm2-pg ce dir: bad spec for {init}")
            mod = _ilu.module_from_spec(spec)
            _sys.modules["_lcm2_pg_ce_pkg"] = mod
            spec.loader.exec_module(mod)
            return mod
    raise FileNotFoundError(
        "lcm2-pg context-engine dir: plugin package __init__.py not found "
        "(set LCM2_PLUGIN_DIR)")


try:
    LCM2PgContextEngine = _package().LCM2PgContextEngine
except Exception:  # package unreachable: stay inert, host falls back loudly
    LCM2PgContextEngine = None


def register(ctx):
    """Category-capture entry (context engines): the bridge engine.

    Import the bridge through the package (single file-anchored
    definition; the package anchors ITS bare siblings — engine, pgconn,
    tools, schemas — by file location before any of them import each
    other).

    X-1 engine-slot precedence: when the legacy lcm-pg plugin is also
    installed, the explicit rule in engine_slot.py decides the slot —
    on a LOSS this entry logs the ONE yield line and skips a doomed
    claim; on a WIN against an already-claimed slot it retires the rival
    lease and claims here. Single-stack: unchanged, one claim."""
    claim = LCM2PgContextEngine
    if claim is None:
        return
    pkg = _package()
    try:
        slot = pkg.engine_slot
    except Exception:
        slot = None

    def _slot_attempt():
        ctx.register_context_engine(claim())

    import logging
    if slot is None:
        _slot_attempt()
    else:
        slot.enforce_slot(ctx, "lcm2-pg", _slot_attempt,
                          logging.getLogger("lcm2-pg"))
