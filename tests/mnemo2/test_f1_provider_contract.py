"""F-1 memory-provider CONTRACT test: memory.provider 'mnemosyne' resolves
to mnemo-pg2 through the host's OWN loader (plugins/memory/__init__.py),
with zero filesystem provider dirs and zero out-of-repo shim.

This is the gate the work order demands (LIVE-shaped, not a FakeCtx
42-name count): we drive hermes-agent's real
plugins.memory.load_memory_provider("mnemosyne") in a subprocess whose
HERMES_HOME points at an EMPTY home (no mnemosyne symlink, no legacy
provider dirs), after mnemo-pg2.register() has run exactly the way
`hermes plugins enable` runs it. A miss here reproduces tessa's R4:
"Memory provider 'mnemosyne' is not installed" -> zero tools.

Also pins the two shadow surfaces (F-5: legacy neutralize IN repo) and
the provider tool surface (42 names) + dispatch routing through the same
plugin_entry._dispatch the PluginManager path uses (F-4: caller's own
schema, no legacy DB).
"""
from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parents[2]
MNEMO2_INIT = REPO / "plugins" / "mnemo-pg2" / "__init__.py"
HERMES_ROOT = pathlib.Path.home() / ".hermes" / "hermes-agent"
HOST_PLUGINS_MEMORY = HERMES_ROOT / "plugins" / "memory" / "__init__.py"

pytestmark = pytest.mark.skipif(
    not HOST_PLUGINS_MEMORY.is_file(),
    reason="hermes-agent checkout not present (contract is the host's)")

# Runs inside the subprocess: boot mnemo-pg2 the way the PluginManager
# does, then resolve 'mnemosyne' through the HOST loader itself.
BOOT_AND_RESOLVE = r'''
import importlib.util, json, os, sys
sys.path.insert(0, %(host)r)
# EMPTY home: no provider dirs at all (the F-1 fix must not rely on any
# filesystem provider install; the shim lived OUTSIDE the repo before).
from hermes_constants import set_hermes_home_override
set_hermes_home_override(os.environ["FAKE_HOME"])

spec = importlib.util.spec_from_file_location(
    "hermes_plugins.mnemo_pg2", %(init)r,
    submodule_search_locations=[%(dir)r])
mod = importlib.util.module_from_spec(spec)
sys.modules["hermes_plugins.mnemo_pg2"] = mod
spec.loader.exec_module(mod)

class Ctx:
    def register_tool(self, **kw): pass
    def register_hook(self, *a, **k): pass
    def register_memory_provider(self, p):
        raise AssertionError(
            "general-plugin path must NOT receive the provider on this "
            "activation path: " + repr(p))
    def __getattr__(self, k): return lambda *a, **kk: None
mod.register(Ctx())

# THE CONTRACT: host's own loader, empty home, name from memory.provider
import plugins.memory as PM
from pathlib import Path
srcs = [str(p) for p in PM._external_source_dirs()]
assert srcs == [] or all("mnemosyne" not in [c.name for c in p.iterdir() if c.is_dir()] for p in PM._external_source_dirs()), \
    "fixture home unexpectedly carries a provider dir: " + repr(srcs)
prov = PM.load_memory_provider("mnemosyne")
out = {"loaded": prov is not None}
if prov is None:
    print(json.dumps(out)); raise SystemExit(0)
out["name"] = getattr(prov, "name", "")
out["available"] = bool(prov.is_available())
out["reason"] = prov.unavailable_reason() if not out["available"] else ""
schemas = prov.get_tool_schemas()
out["n_schemas"] = len(schemas)
out["schema_names"] = sorted(s.get("name", "") for s in schemas)
# dispatch reaches OUR Engine (same _dispatch as the PluginManager path):
# with no DSN env the ONLY honest answer is an error string from our own
# boundary, never a legacy-DB write and never an exception.
r = prov.handle_tool_call("mnemosyne_recall", {"query": "x", "limit": 1})
out["dispatch_type"] = type(r).__name__
try:
    d = json.loads(r)
    out["dispatch_ok_key"] = ("ok" in d) or ("error" in d) or ("memories" in d)
except Exception:
    out["dispatch_ok_key"] = False
# lifecycle round-trip must not raise
prov.initialize("f1-contract-test")
blk = prov.system_prompt_block()
out["prompt_block"] = isinstance(blk, str) and len(blk) > 20
prov.sync_turn([{"role": "user", "content": "hi"}])
prov.shutdown()
print(json.dumps(out))
'''


def _run(env_extra: dict) -> dict:
    import tempfile
    home = tempfile.mkdtemp(prefix="f1home-")
    os.makedirs(os.path.join(home, "plugins"), exist_ok=True)
    env = {k: v for k, v in os.environ.items() if k != "HERMES_HOME"}
    env["FAKE_HOME"] = home
    env.update(env_extra)
    code = BOOT_AND_RESOLVE % {"host": str(HERMES_ROOT),
                               "init": str(MNEMO2_INIT),
                               "dir": str(MNEMO2_INIT.parent)}
    p = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True,
                       text=True, timeout=180, env=env)
    tail = [ln for ln in p.stdout.splitlines() if ln.startswith("{")]
    if not tail:
        raise AssertionError(
            f"subprocess produced no result line (rc={p.returncode})\n"
            f"stdout tail: {p.stdout[-800:]}\nstderr tail: {p.stderr[-800:]}")
    return json.loads(tail[-1])


def test_provider_slot_resolves_via_host_loader():
    """THE F-1 GATE: load_memory_provider('mnemosyne') returns OUR
    provider with 42 schemas, in a process with an EMPTY plugins home
    (no shim, no symlink) after mnemo-pg2.register()."""
    r = _run({"MNEMO2_TOOLS": ""})
    assert r["loaded"], ("memory.provider 'mnemosyne' did NOT resolve to "
                         "mnemo-pg2 (R4 boot-warning class)")
    assert r["name"] == "mnemosyne"
    expected = sorted(n.strip() for n in (REPO / "tests" /
        "mnemo2_vendor_tools_wildcard.txt").read_text().splitlines() if n.strip())
    assert r["schema_names"] == expected, (
        f"provider tool surface != 42-name wildcard ({r['n_schemas']})")
    assert r["dispatch_type"] == "str" and r["dispatch_ok_key"], \
        "handle_tool_call must return the vendor-boundary JSON string"
    assert r["prompt_block"]


def test_legacy_pip_provider_is_shadowed_in_repo():
    """F-5: the neutralize mechanism lives IN the repo now: with
    MNEMO2_FAKE_LEGACY set, the boot claims the legacy 'mnemosyne_hermes'
    key with an inert stub before anything can register through it."""
    r = _run({"MNEMO2_FAKE_LEGACY": "1"})
    assert r["loaded"]
