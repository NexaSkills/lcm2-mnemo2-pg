"""D-2 regression: a LIVE legacy provider binding must never survive
mnemo-pg2's shadowing (both lanes).

Mechanism (template DBPROOF): the pip distributions mnemosyne-hermes /
mnemosyne-memory ship a package literally named ``mnemosyne`` carrying
the legacy sqlite-era provider. Once any host import (plugin scan,
provider warm-up, entry-point load) binds it under the bare key, the
memory provider slot can resolve straight from ``sys.modules`` and
legacy answers agent calls — the zero-fallback :54331 path never runs.
_shadow_legacy() used to SKIP live bindings ("cannot audit"); it now
audits the bound file's CONTENT and quarantines a real provider under
``_mnemo2_quarantine.<key>`` instead.

Also pins scripts/provider_file_assert.sh (the check-time assertion the
brief demands: the serveable provider __file__ resolves under the repo).
"""
import importlib.util
import os
import pathlib
import subprocess
import sys

REPO = pathlib.Path(__file__).resolve().parents[2]
MP_FILE = REPO / "plugins" / "mnemo-pg2" / "memory_provider.py"


def _load_mp():
    sp = importlib.util.spec_from_file_location("_d2_mp_under_test", MP_FILE)
    mod = importlib.util.module_from_spec(sp)
    sp.loader.exec_module(mod)
    return mod


def _fake_provider_file(tmp_path, name="legacy_pkg"):
    pkg = tmp_path / name
    pkg.mkdir()
    (pkg / "__init__.py").write_text(
        "class MemoryProvider:\n    def register_memory_provider(ctx):\n"
        "        raise AssertionError('legacy must not answer')\n")
    return pkg


def test_shadow_legacy_quarantines_live_legacy(monkeypatch, tmp_path):
    pkg = _fake_provider_file(tmp_path)
    monkeypatch.delenv("MNEMO2_FAKE_LEGACY", raising=False)
    saved = dict(sys.modules)
    import types
    leg = types.ModuleType("mnemosyne")
    leg.__file__ = str(pkg / "__init__.py")
    leg.__path__ = [str(pkg)]
    sys.modules["mnemosyne"] = leg
    try:
        _load_mp()._shadow_legacy()
        assert "mnemosyne" not in sys.modules, (
            "live legacy binding survived _shadow_legacy (D-2 class)")
        assert sys.modules.get("_mnemo2_quarantine.mnemosyne") is leg
        assert getattr(leg, "_mnemo2_quarantined_by", "") == "mnemo-pg2"
    finally:
        for k in set(sys.modules) - set(saved):
            sys.modules.pop(k, None)
        for k, v in saved.items():
            if sys.modules.get(k) is not v:
                sys.modules[k] = v


def test_shadow_legacy_skips_our_published_package(monkeypatch, tmp_path):
    monkeypatch.delenv("MNEMO2_FAKE_LEGACY", raising=False)
    saved = dict(sys.modules)
    import types
    pub = types.ModuleType("_hermes_memory_providers.mnemosyne")
    pub.__name__ = pub.__name__
    pub.__file__ = str(MP_FILE)  # OUR file (contains MemoryProvider text)
    sys.modules["mnemosyne"] = pub
    sys.modules["_hermes_memory_providers"] = types.ModuleType(
        "_hermes_memory_providers")
    try:
        _load_mp()._shadow_legacy()
        assert sys.modules.get("mnemosyne") is pub, (
            "our published provider package must never be quarantined")
    finally:
        for k in set(sys.modules) - set(saved):
            sys.modules.pop(k, None)
        for k, v in saved.items():
            if sys.modules.get(k) is not v:
                sys.modules[k] = v


def test_shadow_legacy_exempt_for_fake_fixture(monkeypatch, tmp_path):
    monkeypatch.setenv("MNEMO2_FAKE_LEGACY", "1")
    saved = dict(sys.modules)
    import types
    leg = types.ModuleType("mnemosyne_hermes")
    leg.__file__ = str(_fake_provider_file(tmp_path, "fake") / "__init__.py")
    sys.modules["mnemosyne_hermes"] = leg
    try:
        _load_mp()._shadow_legacy()
        assert sys.modules.get("mnemosyne_hermes") is leg, (
            "MNEMO2_FAKE_LEGACY fixture must stay claimed, not quarantined")
        assert getattr(leg, "_mnemo2_shadow", False)
    finally:
        for k in set(sys.modules) - set(saved):
            sys.modules.pop(k, None)
        for k, v in saved.items():
            if sys.modules.get(k) is not v:
                sys.modules[k] = v


def test_provider_file_assert_script_green():
    """Check-time assertion: the serveable 'mnemosyne' provider __file__
    resolves under THIS repo (loader, not legacy dir/pip provider)."""
    r = subprocess.run(
        ["bash", str(REPO / "scripts" / "provider_file_assert.sh")],
        capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, (
        f"provider_file_assert FAILED: {r.stdout}{r.stderr}")
    assert "ASSERT PASS" in r.stdout
