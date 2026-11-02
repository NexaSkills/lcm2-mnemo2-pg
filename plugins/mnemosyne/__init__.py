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


def register_memory_provider(ctx):
    mod = _published()
    if mod is None:
        # Provider slot asked BEFORE mnemo-pg2's general-plugin
        # registration ran: load the contract file BY PATH (package-
        # qualified, own-file; never the plugin package's __init__) and
        # install, then yield the same instance.
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "_mnemo2_provider_bridge.memory_provider",
            __import__("os").path.join(_REPO, "plugins", "mnemo-pg2",
                                       "memory_provider.py"))
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
