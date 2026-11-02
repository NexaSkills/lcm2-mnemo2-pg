"""F-1 close item d: vendor-parity one-liners for the mnemo-pg2 surface.

These are convenience wrappers over the 42-tool surface, NOT a second
API: each parses its input, calls the SAME plugin_entry._dispatch the
tools use (one provider object, one Engine, one schema per agent), and
returns the vendor-boundary JSON string. Nothing here bypasses the
binding/env discipline (R8) or writes outside the caller's schemas.
"""
from __future__ import annotations

import json
import os


def _dispatch(tool_name: str, args: dict) -> str:
    # Late import through OUR package (never a bare key): the provider
    # lane owns plugin_entry's engine pin (latebind).
    from . import plugin_entry as _pe  # package-qualified
    return _pe._dispatch(tool_name, dict(args or {}))


def remember(content: str, *, importance: float = 0.5,
             scope: str = "session", bank: str = "private") -> str:
    """Store one durable memory (vendor `remember` one-liner)."""
    return _dispatch("mnemosyne_remember",
                     {"content": content, "importance": importance,
                      "scope": scope, "bank": bank})


def recall(query: str, *, limit: int = 5, temporal_weight: float = 0.0) -> str:
    """Hybrid-ranked recall (vendor `recall` one-liner)."""
    return _dispatch("mnemosyne_recall",
                     {"query": query, "limit": limit,
                      "temporal_weight": temporal_weight})


def stats() -> str:
    """Memory counts per tier (vendor `stats` one-liner)."""
    return _dispatch("mnemosyne_stats", {})


def batch_remember(items: list[dict]) -> str:
    """Atomic multi-write (vendor `batch` one-liner): items are
    {action: remember|update|forget|invalidate, ...} dicts."""
    return _dispatch("mnemosyne_batch", {"operations": list(items or [])})


def shared_remember(content: str, *, kind: str = "meta",
                    importance: float = 0.8) -> str:
    """Cross-agent surface write (vendor `shared_remember` one-liner)."""
    return _dispatch("mnemosyne_shared_remember",
                     {"content": content, "kind": kind,
                      "importance": importance})


def task_progress(action: str = "get", task: str = "", state: str = "",
                  metadata: dict | None = None) -> str:
    """Cross-session task state (vendor `task_progress` one-liner)."""
    return _dispatch("mnemosyne_task_progress",
                     {"action": action, "task": task, "state": state,
                      "metadata": metadata or {}})


# ---------------------------------------------------------------- CLI
def _main(argv=None):
    """python -m plugins.mnemo-pg2.oneliners remember 'text' [--importance 0.8]
    (hermes mnemosyne <verb> routes here via the provider cli contract)."""
    import argparse
    p = argparse.ArgumentParser(prog="mnemosyne",
                                description="mnemo-pg2 one-liners")
    p.add_argument("verb", choices=("remember", "recall", "stats",
                                    "shared-remember", "task-progress"))
    p.add_argument("arg", nargs="?", default="")
    p.add_argument("--importance", type=float, default=0.5)
    p.add_argument("--limit", type=int, default=5)
    p.add_argument("--scope", default="session")
    a = p.parse_args(argv)
    if a.verb == "remember":
        out = remember(a.arg, importance=a.importance, scope=a.scope)
    elif a.verb == "recall":
        out = recall(a.arg, limit=a.limit)
    elif a.verb == "shared-remember":
        out = shared_remember(a.arg, importance=a.importance)
    elif a.verb == "task-progress":
        out = task_progress("get", a.arg)
    else:
        out = stats()
    try:
        print(json.dumps(json.loads(out), indent=2, ensure_ascii=False))
    except Exception:
        print(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
