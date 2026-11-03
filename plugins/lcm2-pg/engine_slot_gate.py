#!/usr/bin/env bash
# LCM2-FLEET-SPRINT-1 X-1 — engine_slot_gate.py: legacy-lane (lcm-pg)
# integration with the ONE engine-slot precedence rule.
#
# This file is NOT an overlay and does NOT patch or replace the legacy
# plugin's code. It ships inside the lcm2-pg plugin dir (visible to the
# legacy lane as <HERMES_HOME>/plugins/lcm2-pg/engine_slot_gate.py —
# the legacy lane may import stdlib + locate this file BY PATH only, no
# package import). The legacy lane's own register() decides whether to
# consult it at all; absent/unconsulted = exactly the pre-X-1 legacy
# behavior. On a legacy-side LOSS the gate logs the ONE yield line and
# returns WITHOUT calling register_self (the legacy claim is skipped by
# ITS OWN code path, not by anything mutating the legacy module).
#
# Rule lives in engine_slot.py beside this file (single source).
#
# Usage from the legacy lane (its code, not ours):
#     gate = load_by_path(<...>/lcm2-pg/engine_slot_gate.py)
#     token = gate.gate(ctx, register_self)   # register_self = the
#                                             # legacy lane's own normal
#                                             # ctx.register_context_engine
#                                             # call, zero-arg
# Token LCM2-FLEET-SPRINT-1-X1
# -----------------------------------------------------------------------
import importlib.util
import logging
import os

_log = logging.getLogger("lcm-pg")


def _load_rule():
    """Load the shared resolver from THIS gate's own directory (it
    ships beside engine_slot.py inside the lcm2-pg plugin dir), with
    NO package imports — engine_slot.py is dependency-free stdlib."""
    here = os.path.dirname(os.path.abspath(__file__))
    f = os.path.join(here, "engine_slot.py")
    if not os.path.isfile(f):
        return None
    try:
        spec = importlib.util.spec_from_file_location(
            "_lcm2_pg_engine_slot_rule", f)
        if spec is None or spec.loader is None:
            return None
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        if hasattr(mod, "enforce_slot"):
            return mod
    except Exception:
        return None
    return None


def gate(ctx, register_self):
    """Wrap a legacy-lane ctx.register_context_engine call.

    Returns the enforce_slot diagnostic token, or 'rule-absent' when
    the rule module cannot be located (legacy path then runs exactly
    as pre-X-1)."""
    rule = _load_rule()
    if rule is None:
        return "rule-absent"
    try:
        return rule.enforce_slot(ctx, "lcm-pg", register_self, _log)
    except Exception as exc:  # never break legacy boot over the rule
        _log.warning("lcm-pg: engine-slot rule unavailable (%s); "
                     "registration order stands", exc)
        register_self()
        return "rule-error"
