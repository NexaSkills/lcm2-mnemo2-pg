"""GAP-H2: engine._threshold() must obey the profile's compression.threshold.

Vendor reality (Tessa's regap, W3BEEZREGAP-TESSA-1635): the old
'from agent_config import get_lcm_context_threshold' route never existed
vendor-wide (no agent_config.py anywhere in hermes-agent), so engine
DEFAULT 0.75 silently won no matter what config said. The cure:
_yamlget.get_config_key('compression.threshold') — stdlib text-first
reader over the LIVE profile config.yaml (hermes_constants home rule,
same profile-scoping as pgconn.agent_schema).

Priority proved here: LCM_CONTEXT_THRESHOLD env > config compression.
threshold > DEFAULT_THRESHOLD (0.75).
"""
import importlib.util
import os
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PLUGIN_DIR = os.path.join(REPO, "plugins", "lcm2-pg")
if PLUGIN_DIR not in sys.path:
    sys.path.insert(0, PLUGIN_DIR)


def _load(name):
    """File-anchored load (mirrors _selfimport.anchor) so the test never
    binds a sibling suite's copy of a bare name."""
    full = f"_h2test_{name}"
    mod = sys.modules.get(full)
    if mod is not None:
        return mod
    spec = importlib.util.spec_from_file_location(
        full, os.path.join(PLUGIN_DIR, name + ".py"))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[full] = mod
    spec.loader.exec_module(mod)
    return mod


_yamlget = _load("_yamlget")


@pytest.fixture
def fake_home(tmp_path, monkeypatch):
    """Point the live-home resolution at a throwaway profile dir by
    FORCING the hermes_constants route to fail (except-arm => HERMES_HOME
    env), exactly the non-Hermes-interpreter behaviour pgconn uses too."""
    monkeypatch.setitem(sys.modules, "hermes_constants", None)  # import raises
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    return tmp_path


def _write_config(home, text):
    (home / "config.yaml").write_text(text, encoding="utf-8")


def test_config_value_wins_over_default(fake_home):
    _write_config(fake_home,
                  "compression:\n  enabled: true\n  threshold: 0.5\n")
    assert _yamlget.get_config_key("compression.threshold") == "0.5"


def test_engine_effective_threshold_is_configured_050(fake_home):
    """THE gap assertion: update_model on a live engine yields
    threshold_tokens == context_length * CONFIG value (0.50), not 0.75."""
    _write_config(fake_home,
                  "compression:\n  enabled: true\n  threshold: 0.5\n")
    eng = _load("engine")
    e = eng.LCM2Engine.__new__(eng.LCM2Engine)  # no DB bootstrap needed:
    # _threshold/update_model touch no connection (clone-shape probe only)
    assert e._threshold.__func__(e) == 0.50
    e.update_model("probe-model", 200_000)
    assert e.threshold_tokens == 100_000  # 200k * 0.50, NOT 150k (* 0.75)
    assert e.should_compress(prompt_tokens=100_000) is True
    assert e.should_compress(prompt_tokens=99_999) is False


def test_env_still_overrides_config(fake_home):
    _write_config(fake_home, "compression:\n  threshold: 0.5\n")
    os.environ["LCM_CONTEXT_THRESHOLD"] = "0.9"
    try:
        eng = _load("engine")
        e = eng.LCM2Engine.__new__(eng.LCM2Engine)
        assert e._threshold.__func__(e) == 0.9
    finally:
        del os.environ["LCM_CONTEXT_THRESHOLD"]


def test_missing_key_falls_back_to_default(fake_home):
    _write_config(fake_home, "compression:\n  enabled: true\nmodel: x\n")
    assert _yamlget.get_config_key("compression.threshold") is None
    eng = _load("engine")
    e = eng.LCM2Engine.__new__(eng.LCM2Engine)
    assert e._threshold.__func__(e) == eng.DEFAULT_THRESHOLD == 0.75


def test_wrong_block_never_leaks(fake_home):
    # a `threshold:` under ANY other top-level block must not be read
    _write_config(fake_home,
                  "auxiliary:\n  compression:\n    threshold: 0.9\n"
                  "compression:\n  enabled: true\n")
    assert _yamlget.get_config_key("compression.threshold") is None


def test_no_config_file_is_safe(fake_home):
    assert _yamlget.get_config_key("compression.threshold") is None


def test_flow_style_config(fake_home):
    _write_config(fake_home, "compression: {enabled: true, threshold: 0.5}\n")
    assert _yamlget.get_config_key("compression.threshold") == "0.5"
