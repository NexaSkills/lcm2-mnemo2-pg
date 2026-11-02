#!/usr/bin/env python3
"""Neutral provider dir for mnemo-pg2 (F-1 close item c).

This package is NOT the provider implementation. The host's memory
provider loader (plugins/memory/__init__.py::_load_provider_from_dir)
discovers providers by DIRECTORY name under $HERMES_HOME/plugins/,
imports that dir under a synthetic namespace, and runs its ``register``.
mnemo-pg2's real implementation is published in-memory as
``_hermes_memory_providers.mnemosyne`` (see plugins/mnemo-pg2/
memory_provider.py); importing plugins/mnemo-pg2/__init__.py through the
host's synthetic loader instead would run that module's exec twice —
which rewrites sys.modules keys the general lane owns — so this thin
dir only bridges the two: it locates the REAL plugin package by path
and asks its published provider module to hand the ONE provider
instance to the collector.

onboard_profile.sh authors the discovery entry as a symlink
$HERMES_HOME/plugins/mnemosyne -> THIS directory (memory_provider.py's
_provider_dir_shim keeps it pointed here idempotently); the symlink
lives in the profile home, never in the repo checkout (F-5-clean).
This directory is deliberately NOT next to plugins/mnemo-pg2 under the
repo's plugins/ root: the general PluginManager scans that root too, and
an entry named "mnemosyne" there would import the legacy pip package
instead (import-shadowing class, F-1 root-cause discipline).
"""
from __future__ import annotations

import sys

_HERE = __import__("os").path.dirname(__import__("os").path.abspath(__file__))
_REPO = __import__("os").path.dirname(__import__("os").path.dirname(_HERE))


def _published():
    """The provider module memory_provider.install() published (in-memory
    package), or None. Never imports the plugin package itself."""
    return sys.modules.get("_hermes_memory_providers.mnemosyne")


def _contract_path():
    """OUR plugins/mnemo-pg2/memory_provider.py, located from THIS file.

    Path-anchored, not repo-anchored: the discovery symlink may sit at
    any depth (profile plugins/, machine ~/.hermes/plugins/), so walking
    parents off _REPO misresolves. This file lives at
    <root>/plugins/mnemosyne/__init__.py and the plugin package is the
    SIBLING dir <root>/plugins/mnemo-pg2/ at the same depth — under
    symlink resolution both on-disk layouts (repo checkout and any
    deploy that mirrors the plugins/ root) resolve identically.
    """
    return __import__("os").path.normpath(
        __import__("os").path.join(_HERE, __import__("os").path.pardir,
                                   "mnemo-pg2", "memory_provider.py"))


def _legacy_quarantine():
    """D-2 self-heal: a LIVE legacy provider module under a bare key.

    The pip distributions ``mnemosyne-hermes`` (entry point
    ``mnemosyne -> mnemosyne_hermes``) and ``mnemosyne-memory`` ship a
    package literally named ``mnemosyne`` with a provider class inside.
    Once ANY import bound one of those files under the bare ``mnemosyne``
    or ``mnemosyne_hermes`` key (a plugin scan, a provider warm-up
    import, an entry-point load — the host's
    ``import_memory_provider_module`` does exactly this), a dir-less
    loader resolves the slot from ``sys.modules`` and the LEGACY
    sqlite-era provider answers the agent — the zero-fallback
    :54331 path never runs (DBPROOF class). The inert shadows
    memory_provider._shadow_legacy installs only cover keys that were
    FREE at install time; a live legacy binding needs a move-aside,
    which only an OUR-dir file may perform — this shim is that file,
    present at every activation route.

    Policy: never touch OUR published package
    (``_hermes_memory_providers.mnemosyne``), an existing
    ``_mnemo2_shadow`` stub, or an unclassifiable module (no
    __file__/unreadable). A legacy file moves aside under a
    namespaced quarantine key; a bare ``mnemosyne`` key left unowned
    is not created (find_spec must not import) — _shadow_legacy
    covers the free-key case.
    """
    try:
        pub = sys.modules.get("_hermes_memory_providers.mnemosyne")
        for key in ("mnemosyne", "mnemosyne_hermes"):
            m = sys.modules.get(key)
            if m is None or m is pub or getattr(m, "_mnemo2_shadow", False):
                continue
            f = getattr(m, "__file__", None)
            if not isinstance(f, str) or not f:
                continue
            try:
                with open(f, encoding="utf-8", errors="replace") as fh:
                    src = fh.read(200000)
            except OSError:
                continue
            if "MemoryProvider" in src or "register_memory_provider" in src:
                sys.modules[f"_mnemo2_quarantine.{key}"] = m
                m._mnemo2_quarantined_by = "mnemo-pg2"
                del sys.modules[key]
    except Exception:
        pass  # belt: the activation itself must never hinge on this


def register_memory_provider(ctx):
    mod = _published()
    if mod is None:
        # Provider slot asked BEFORE mnemo-pg2's general-plugin
        # registration ran: load the contract file BY PATH (package-
        # qualified, own-file; never the plugin package's __init__) and
        # install, then yield the same instance.
        _legacy_quarantine()  # D-2 belt (install() below quarantines too)
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "_mnemo2_provider_bridge.memory_provider", _contract_path())
        if spec is not None and spec.loader is not None:
            holder = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = holder
            try:
                spec.loader.exec_module(holder)
                holder.install()
            finally:
                sys.modules.pop(spec.name, None)
            mod = _published()
    if mod is not None:
        mod.register_memory_provider(ctx)


def register(ctx):
    register_memory_provider(ctx)
    # F-1 boot-order immunity: directory discovery may resolve the slot
    # BEFORE the general lane imports plugins/mnemo-pg2 (and even if it
    # imported it, register() may not have run). The provider object is
    # long-lived and its dispatch reaches engine.py, whose bare
    # `import pgconn` resolves at CONNECT time with no of-dir frames in
    # the chain — every late window must see OUR bindings. install()
    # already pinned once; re-pin here so the pin's provenance is
    # boot-order-independent (idempotent: setattrs to identity).
    try:
        mp = sys.modules.get("hermes_plugins.mnemo_pg2.memory_provider") \
            or _published()
        lb = getattr(mp, "_latebind", None) if mp is not None else None
        if lb is not None:
            lb()  # noqa: re-resolve latebind (in-memory pkg or by file)
    except Exception:
        pass
