"""Bridge attribute mirror (zero-window cure, 2026-11-05).

Host reality (agent/context_engine.py): the ContextEngine BASE concretely
declares last_prompt_tokens/threshold_tokens/context_length/compression_count/
... = 0. A delegation-only wrapper (methods forwarded, __getattr__ for the
rest) can NEVER surface the impl's values for those names — normal lookup
finds the base's zero first, __getattr__ never fires. The host then displays
"context window 0" while the plugin engine holds the real numbers.

Cure: LCM2PgContextEngine mirrors the host-read names with explicit
properties delegating to the impl. Proved here WITHOUT any DB bootstrap
(clone-shape pattern from test_h2_config_threshold.py): wrapper built via
__new__, impl via __new__, threshold pinned via LCM_CONTEXT_THRESHOLD (beats
config per GAP-H2).
"""
import importlib.util
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))              # tests/lcm2
REPO = os.path.dirname(os.path.dirname(HERE))                  # repo root
PLUGIN_DIR = os.path.join(REPO, "plugins", "lcm2-pg")
_HOST_ROOT = os.path.expanduser("~/.hermes/hermes-agent")

_KEYS = ("_mirror_bridge", "_mirror_engine")


def _cleanup():
    for k in _KEYS:
        sys.modules.pop(k, None)


def _load_file(key, path):
    spec = importlib.util.spec_from_file_location(key, path)
    assert spec is not None and spec.loader is not None, f"no spec for {path}"
    mod = importlib.util.module_from_spec(spec)
    sys.modules[key] = mod
    spec.loader.exec_module(mod)
    return mod


def _load():
    """Bridge + engine file-anchored (mirrors _selfimport; the shim's
    ContextEngine base needs the host repo importable, same contract as
    test_t1_bridge_key_import_order)."""
    if os.path.isdir(_HOST_ROOT) and _HOST_ROOT not in sys.path:
        sys.path.append(_HOST_ROOT)
    _cleanup()
    # engine first: the bridge anchors its own impl through _impl_class(),
    # but THIS test drives the impl shell directly (no DB constructor).
    eng = _load_file("_mirror_engine", os.path.join(PLUGIN_DIR, "engine.py"))
    br = _load_file("_mirror_bridge", os.path.join(PLUGIN_DIR, "context_engine_shim.py"))
    return br, eng


def _shell_pair(br, eng):
    w = br.LCM2PgContextEngine.__new__(br.LCM2PgContextEngine)
    e = eng.LCM2Engine.__new__(eng.LCM2Engine)
    object.__setattr__(w, "_impl", e)
    return w, e


def test_context_length_mirrors_after_update_model(monkeypatch):
    br, eng = _load()
    try:
        monkeypatch.setenv("LCM_CONTEXT_THRESHOLD", "0.5")
        w, e = _shell_pair(br, eng)
        w.update_model("main", 262144)
        assert e.context_length == 262144          # impl got it (pre-existing)
        assert w.context_length == 262144          # HOST read — the cure
        assert w.threshold_tokens == 131072        # 262144 * 0.5
        assert w.should_compress(140000) is True
        assert w.should_compress(100000) is False
    finally:
        _cleanup()


def test_usage_counters_mirror(monkeypatch):
    br, eng = _load()
    try:
        monkeypatch.setenv("LCM_CONTEXT_THRESHOLD", "0.5")
        w, e = _shell_pair(br, eng)
        w.update_model("main", 200000)
        w.update_from_response({"prompt_tokens": 1234, "completion_tokens": 20,
                                "total_tokens": 1254})
        assert w.last_prompt_tokens == 1234
        assert w.last_completion_tokens == 20
        assert w.last_total_tokens == 1254
        assert w.last_real_prompt_tokens == 1234
        assert w.compression_count == getattr(e, "compression_count", 0)
    finally:
        _cleanup()


def test_host_writes_reach_the_impl(monkeypatch):
    br, eng = _load()
    try:
        monkeypatch.setenv("LCM_CONTEXT_THRESHOLD", "0.5")
        w, e = _shell_pair(br, eng)
        # agent_init writes model_thresholds on the SELECTED engine instance.
        w.model_thresholds = {"gpt-9": 0.3}
        assert e.model_thresholds == {"gpt-9": 0.3}
        # context_switch_guard-style reads must never see the base's zeros.
        assert w.protect_first_n == getattr(e, "protect_first_n", 3)
        assert w.protect_last_n == getattr(e, "protect_last_n", 6)
        assert isinstance(type(w).context_length, property)
        assert isinstance(type(w).threshold_tokens, property)
    finally:
        _cleanup()
