"""S5 follow-up (beez live finding 2026-11-06): a transient fastembed boot
failure must NOT permanently pin the process to hashed. Live symptom: boot-
time model-load stampede failed once; every write after that silently said
hashed256 for the whole process lifetime."""
import sys
import pathlib
import time

import pytest

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "plugins" / "mnemo-pg2"))

import embed as _embed_mod  # noqa: E402


def test_cooldown_is_retryable(monkeypatch):
    monkeypatch.setenv("MNEMO2_EMBED_MODEL", "fastembed")
    monkeypatch.setattr(_embed_mod, "_fe_model", None)
    monkeypatch.setattr(_embed_mod, "_state", dict(
        _embed_mod._state, next_try=time.monotonic() + 600, warned=True))
    # inside cooldown: hashed fallback, no exception
    v = _embed_mod.embed_text("fog horn on the headland sounds thrice")
    assert len(v) == 256
    # cooldown expired: another attempt happens (loads or re-defers)
    _embed_mod._state["next_try"] = 0.0
    v2 = _embed_mod.embed_text("fog horn on the headland sounds thrice")
    if _embed_mod.warm():  # fastembed usable on this host
        assert len(v2) == 384
    else:
        assert len(v2) == 256  # still down, but RETRIED, not pinned


def test_runtime_break_drops_instance_for_reload(monkeypatch):
    class Boom:
        def embed(self, texts):
            raise RuntimeError("onnx boom")
    monkeypatch.setenv("MNEMO2_EMBED_MODEL", "fastembed")
    monkeypatch.setattr(_embed_mod, "_fe_model", Boom())
    monkeypatch.setattr(_embed_mod, "_state", dict(
        _embed_mod._state, next_try=0.0))
    assert _embed_mod._fe_encode(["x"], "") is None
    assert _embed_mod._fe_model is None  # instance dropped for reload
    assert _embed_mod._state["next_try"] > time.monotonic()  # cooldown set
