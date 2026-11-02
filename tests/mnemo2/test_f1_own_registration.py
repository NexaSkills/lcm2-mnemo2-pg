"""F-1 regression test: mnemo-pg2 must register ITS OWN 42 mnemosyne_* tools.

Two product root causes pinned (both fixed):

1. LOADER HIJACK: the bare '_selfimport' key is owned by BOTH plugin
   packages (lcm2-pg ships a file-anchored loader under the same key, and
   host boot leaves it bound to lcm2-pg's file). mnemo-pg2.register's
   `from _selfimport import anchor` silently bound lcm2-pg's loader:
   anchor('plugin_entry') resolved lcm2-pg/plugin_entry.py against lcm2's
   _HERE, so mnemo-pg2 registered lcm2's 15 lcm_* tools and ZERO
   mnemosyne_* tools. Fix: register() resolves OUR _selfimport first
   (_selfimport_module(): package-qualified / namespaced key), never the
   bare key.

2. DEPLOYED-LAYOUT CRASH: the 42-name list was read ONLY from
   <plugin>/../../tests/mnemo2_vendor_tools_wildcard.txt — two dirs above
   the plugin file. In any deployed layout (profile/global plugin dirs)
   that path does not exist, _wildcard_names() raised FileNotFoundError
   inside register(), the host disposed every registration, and the whole
   plugin loaded with 0 tools. Fix: the list ships INSIDE the plugin dir
   (vendor_tools.txt) with the repo tests path as checkout fallback.

These tests pin the fixes permanently: (a) names ship plugin-locally and
equal the repo's checked-in 42-name list, (b) register() through the
hijack boot state (bare key = lcm2-pg's file) yields 42 mnemosyne_* and
zero lcm_* tools, (c) no mnemosyne_* tool ever lands under a foreign
toolset.
"""
from __future__ import annotations

import importlib.util
import os
import pathlib
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parents[2]
LCM2_INIT = REPO / "plugins" / "lcm2-pg" / "__init__.py"
MNEMO2_INIT = REPO / "plugins" / "mnemo-pg2" / "__init__.py"
WILDCARD = REPO / "tests" / "mnemo2_vendor_tools_wildcard.txt"
LOCAL_TOOLS = REPO / "plugins" / "mnemo-pg2" / "vendor_tools.txt"


class Ctx:
    def __init__(self):
        self.tools = []
        self.hooks = []

    def register_tool(self, name=None, toolset=None, schema=None,
                      handler=None, **kw):
        self.tools.append((name, toolset))

    def register_hook(self, hook, fn=None, **kw):
        self.hooks.append(hook)

    def __getattr__(self, key):
        return lambda *a, **k: None


def _load(pkg: str, init: pathlib.Path):
    spec = importlib.util.spec_from_file_location(
        pkg, str(init), submodule_search_locations=[str(init.parent)])
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[pkg] = mod
    spec.loader.exec_module(mod)
    return mod


def _purge_plugin_keys():
    """Drop every module key the two plugin packages own (fresh boot
    state per test). Stdlib/other bindings are never touched."""
    ours = []
    for k in list(sys.modules):
        if k.startswith(("_lcm2_", "_mnemo2_", "hermes_plugins.",
                         "_lcm2_pg", "lcm2_pg", "_selfimport")) or k in {
                "engine", "schemas", "hooks", "pgconn", "ddl", "command",
                "plugin_entry", "embed", "rollup_periods"}:
            ours.append(k)
        elif k == "tools":
            f = str(getattr(sys.modules[k], "__file__", ""))
            if str(REPO) in f:  # OUR flat tools.py: drop; host's: keep
                ours.append(k)
    for k in ours:
        sys.modules.pop(k, None)


# ---------------------------------------------------------------- names

def test_names_ship_plugin_locally_and_match_repo_list():
    """Root cause 2 pin: the 42-name list ships INSIDE the plugin dir and
    equals the repo's checked-in wildcard list byte-for-byte."""
    assert LOCAL_TOOLS.is_file(), "plugins/mnemo-pg2/vendor_tools.txt must ship"
    local = sorted(n.strip() for n in LOCAL_TOOLS.read_text().splitlines() if n.strip())
    repo_list = sorted(n.strip() for n in WILDCARD.read_text().splitlines() if n.strip())
    assert len(local) == 42
    assert local == repo_list, "plugin-local list drifted from the repo 42-name list"


def test_wildcard_names_survive_deployed_layout(tmp_path, monkeypatch):
    """Root cause 2 pin: with the repo tests dir UNREACHABLE (standalone
    deployed plugin dir), _wildcard_names() still returns 42 names — no
    FileNotFoundError, no register()-time dispose (F-1 boot state)."""
    _purge_plugin_keys()
    monkeypatch.delenv("MNEMO2_TOOLS", raising=False)
    deploy = tmp_path / "mnemo-pg2"
    import shutil
    shutil.copytree(MNEMO2_INIT.parent, deploy,
                    ignore=shutil.ignore_patterns("__pycache__"))
    mod = _load("mnemo2_deploy_probe", deploy / "__init__.py")
    pe = mod._anchor("plugin_entry")
    names = pe._wildcard_names()
    assert len(names) == 42, f"deployed layout gave {len(names)} names"
    assert all(n.startswith("mnemosyne_") for n in names)


# ---------------------------------------------------------------- hijack

@pytest.fixture()
def hijack_boot(monkeypatch):
    """Boot BOTH plugins in one interpreter the way the host does:
    lcm2-pg imports first and its register() runs BEFORE mnemo-pg2's
    (alphabetical dir order), so the bare '_selfimport' key carries
    lcm2-pg's loader at the moment mnemo-pg2.register() starts — the
    exact F-1 state."""
    _purge_plugin_keys()
    monkeypatch.delenv("MNEMO2_TOOLS", raising=False)
    lcm2 = _load("hermes_plugins.lcm2_pg", LCM2_INIT)
    c1 = Ctx()
    lcm2.register(c1)
    # force the F-1 boot state deterministically: bare key = lcm2's file
    # (whatever the context-engine scan left there in this interpreter)
    lcm2_self = sys.modules.get("_lcm2_impl__selfimport") \
        or sys.modules.get("hermes_plugins.lcm2_pg._selfimport")
    if lcm2_self is not None:
        sys.modules["_selfimport"] = lcm2_self
        assert pathlib.Path(lcm2_self.__file__).parent == LCM2_INIT.parent
    mnemo2 = _load("hermes_plugins.mnemo_pg2", MNEMO2_INIT)
    c2 = Ctx()
    mnemo2.register(c2)
    return c1, c2


def test_f1_registers_own_42_tools_under_hijack(hijack_boot):
    c1, c2 = hijack_boot
    mn = sorted(n for n, ts in c2.tools if str(n).startswith("mnemosyne_"))
    lcm = sorted(n for n, ts in c2.tools if str(n).startswith("lcm_"))
    assert lcm == [], f"mnemo-pg2 registered FOREIGN lcm_* tools (F-1 hijack): {lcm[:5]}"
    expected = sorted(n.strip() for n in WILDCARD.read_text().splitlines() if n.strip())
    assert mn == expected, f"mnemosyne_* surface mismatch: got {len(mn)} (sample {mn[:3]})"


def test_f1_toolset_and_hooks_under_hijack(hijack_boot):
    c1, c2 = hijack_boot
    assert all(ts == "memory" for n, ts in c2.tools
               if str(n).startswith("mnemosyne_")), \
        "mnemosyne_* tools must live in the 'memory' toolset"
    assert sorted(c2.hooks) == ["on_session_start", "post_tool_call", "pre_llm_call"]


def test_f1_lcm2_surface_intact(hijack_boot):
    c1, c2 = hijack_boot
    lcm = sorted(n for n, ts in c1.tools if str(n).startswith("lcm_"))
    assert len(lcm) == 15, f"lcm2-pg lost tools under the shared boot: {len(lcm)}"
