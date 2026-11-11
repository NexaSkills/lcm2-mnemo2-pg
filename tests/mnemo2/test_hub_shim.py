"""_provider_dir_shim vs the PLUGIN-HUB STANDARD (Jan 2026-11-11):
the shim must ACCEPT a real-dir hub entry that is the plugin_hub.sh
materialisation of OUR bridge (carries the .nk-hub-sync marker naming the
source checkout) instead of quarantining it as "old-install legacy" and
replacing it with a symlink — the D-2 leg predates the standard and
otherwise churns the hub on EVERY provider install (observed on
buddyos-nexa 2026-11-11 11:31:28: hub real dir renamed to
backups/mnemosyne-dir-quarantined-* + symlink authored, so
check_mnemo lane 8 flips to drift after every boot).
Foreign marker content (a DIFFERENT source checkout) still quarantines:
the co-existence rule is decided by provenance, not by file existence.
"""
from __future__ import annotations

import importlib.util
import os
import pathlib
import stat
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parents[2]
MP_FILE = REPO / "plugins" / "mnemo-pg2" / "memory_provider.py"
BRIDGE_DIR = REPO / "plugins" / "mnemosyne"
MARK = ".nk-hub-sync"

pytestmark = pytest.mark.skipif(
    not (BRIDGE_DIR / "__init__.py").is_file(),
    reason="bridge dir not shipped in this checkout")


def _load_provider_mod():
    name = "_nk_hub_test.memory_provider"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, MP_FILE)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def _shim(home: pathlib.Path, monkeypatch):
    mod = _load_provider_mod()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.delenv("MNEMO2_BRIDGE_DIR", raising=False)
    # the shim imports get_hermes_home INSIDE the function -> stub the
    # module (the bare test interpreter has no hermes_constants)
    import types
    stub = types.ModuleType("hermes_constants")
    stub.get_hermes_home = lambda: home
    monkeypatch.setitem(sys.modules, "hermes_constants", stub)
    mod._provider_dir_shim()


def _is_symlink(p: pathlib.Path) -> bool:
    try:
        return stat.S_ISLNK(os.lstat(p).st_mode)
    except FileNotFoundError:
        return False


def test_hub_materialized_entry_untouched(tmp_path, monkeypatch):
    home = tmp_path / "home"
    target = home / "plugins" / "mnemosyne"
    target.mkdir(parents=True)
    # faithful materialisation: byte-identical bridge __init__ + marker
    import shutil
    shutil.copy2(BRIDGE_DIR / "__init__.py", target / "__init__.py")
    (target / MARK).write_text(str(REPO) + "\n")
    _shim(home, monkeypatch)
    assert not _is_symlink(target), "shim quarantined the hub-materialised entry"
    assert (target / MARK).is_file(), "materialisation marker lost"
    backups = home / "backups"
    assert not backups.exists() or not list(backups.glob("mnemosyne-dir-quarantined-*"))


def test_foreign_marker_still_quarantined(tmp_path, monkeypatch):
    home = tmp_path / "home"
    target = home / "plugins" / "mnemosyne"
    target.mkdir(parents=True)
    # mutant __init__ (NOT our bridge): provenance fails regardless of marker
    (target / "__init__.py").write_text("raise SystemExit('rogue')\n")
    (target / MARK).write_text("/somewhere/else\n")
    _shim(home, monkeypatch)
    # foreign-provenance real dir: old D-2 policy stands (quarantine + bridge)
    assert _is_symlink(target), "foreign entry must be replaced by our bridge"
    assert os.path.realpath(target) == str(BRIDGE_DIR.resolve())
    backups = home / "backups"
    assert list(backups.glob("mnemosyne-dir-quarantined-*"))


def test_plain_dir_and_absent_still_self_heal(tmp_path, monkeypatch):
    home = tmp_path / "home"
    target = home / "plugins" / "mnemosyne"
    target.mkdir(parents=True)
    (target / "legacy.py").write_text("sqlite-era\n")   # no marker = legacy
    _shim(home, monkeypatch)
    assert _is_symlink(target) and os.path.realpath(target) == str(BRIDGE_DIR.resolve())
    # absent entry -> authored
    home2 = tmp_path / "home2"
    _shim(home2, monkeypatch)
    assert _is_symlink(home2 / "plugins" / "mnemosyne")
