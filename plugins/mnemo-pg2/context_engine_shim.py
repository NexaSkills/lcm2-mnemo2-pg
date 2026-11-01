"""mnemo-pg2 context-engine probe marker (spec-phase3 §3).

mnemo-pg2 is a MEMORY plugin: the real route is register(ctx) with 42
tools + 3 hooks (plugin_entry.py). This file exists so the module-level
marker in __init__.py stays honest WITHOUT pretending to be a context
engine:

  * No context-engine class is exported. The class the host context
    loader probes for (MnemoPg2ContextEngine) is deliberately ABSENT —
    mnemo-pg2 must never be selected as a context engine (that is
    lcm2-pg's job; two lossless engines in one slot is an R10 violation).
  * It is importable and harmless (no host imports, no side effects),
    so importing this file in a shared interpreter cannot strand
    anything, and __init__._load_bridge() resolves the shared
    '_lcm2_pg_bridge.context_engine_shim' key by class presence: when
    lcm2-pg loaded first, the foreign module lacks our class and the
    __init__ purge restores its binding untouched.
"""
from __future__ import annotations

# Explicit marker for humans and the probe-grep: there is NO
# MnemoPg2ContextEngine here BY DESIGN (negative pin, phase3 result D).
__all__: list[str] = []
