"""Surface-bank recall on shared views must qualify temporal columns (R5)."""
import os
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[2]
ENGINE = ROOT / "plugins" / "mnemo-pg2" / "engine.py"


def test_temporal_expr_qualifies_created_at():
    src = ENGINE.read_text(encoding="utf-8")
    assert "GREATEST(" in src
    # the temporal expression must not reference a bare `created_at`
    # (shared_memory VIEW joins would make it ambiguous)
    for frag in src.split("GREATEST(")[1:]:
        inner = frag.split(")", 1)[0]
        assert " - created_at" not in inner, "bare created_at in temporal expr"
