"""MANNY-AUDIT-1940: should_compress_preflight must be threshold-consistent.

Live defect (beez, manny agent.log 96x today): the preflight hook returned
True UNCONDITIONALLY, so the host's engine-driven preflight maintenance
(agent/turn_context_compaction.py:_engine_preflight_maintenance) ran a
compress() pass on EVERY turn regardless of token state — with a degenerate
threshold_tokens=0 (context_length unresolved) every prompt logged
"Preflight compression: ~N tokens >= 0 threshold" and burned
"Max compression attempts (3) reached" / "Context compression failed".

Contract proven here (clone-shape, no DB bootstrap — same technique as
test_h2_config_threshold.py):
  - preflight False BELOW threshold
  - preflight True AT or ABOVE threshold
  - preflight False when update_model never ran (context_length 0) —
    the same rule should_compress(None) already applies.
"""
import importlib.util
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PLUGIN_DIR = os.path.join(REPO, "plugins", "lcm2-pg")
if PLUGIN_DIR not in sys.path:
    sys.path.insert(0, PLUGIN_DIR)


def _load(name):
    """File-anchored load (mirrors _selfimport.anchor) so the test never
    binds a sibling suite's copy of a bare name."""
    full = f"_preflighttest_{name}"
    mod = sys.modules.get(full)
    if mod is not None:
        return mod
    spec = importlib.util.spec_from_file_location(
        full, os.path.join(PLUGIN_DIR, name + ".py"))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[full] = mod
    spec.loader.exec_module(mod)
    return mod


def _engine():
    eng = _load("engine")
    e = eng.LCM2Engine.__new__(eng.LCM2Engine)  # no DB bootstrap needed
    return e


def _fresh(monkeypatch, threshold="0.5"):
    """Deterministic threshold: env pin beats config (GAP-H2 priority)."""
    monkeypatch.setenv("LCM_CONTEXT_THRESHOLD", threshold)
    e = _engine()
    e.update_model("probe-model", 200_000)
    return e


def test_preflight_false_below_threshold(monkeypatch):
    e = _fresh(monkeypatch)
    assert e.threshold_tokens == 100_000  # 200k * 0.50
    e.update_from_response({"prompt_tokens": 99_999})
    assert e.should_compress_preflight([{"role": "user", "content": "hi"}]) is False


def test_preflight_true_at_threshold(monkeypatch):
    e = _fresh(monkeypatch)
    e.update_from_response({"prompt_tokens": 100_000})
    assert e.should_compress_preflight([{"role": "user", "content": "hi"}]) is True


def test_preflight_true_above_threshold(monkeypatch):
    e = _fresh(monkeypatch)
    e.update_from_response({"prompt_tokens": 150_000})
    assert e.should_compress_preflight([{"role": "user", "content": "hi"}]) is True


def test_preflight_false_before_update_model():
    """context_length 0 (update_model never ran): the >=threshold rule is
    unsatisfiable, so preflight must NOT fire — the unconditional-True bug
    fired here and produced the '>= 0 threshold' doom loop."""
    e = _engine()
    assert e.context_length == 0
    assert e.should_compress_preflight([{"role": "user", "content": "hi"}]) is False


def test_preflight_consistent_with_should_compress(monkeypatch):
    """For any token state the preflight answer equals should_compress's —
    the two hooks can never disagree again."""
    e = _fresh(monkeypatch)
    for pt in (0, 1, 99_999, 100_000, 100_001, 250_000):
        e.update_from_response({"prompt_tokens": pt})
        assert e.should_compress_preflight([]) == e.should_compress(None), pt
