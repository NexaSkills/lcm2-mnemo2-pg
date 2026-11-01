"""Surface-bank recall on shared views must qualify temporal columns (R5)."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir,
                                "plugins", "mnemo-pg2"))

import engine as engmod  # noqa: E402


def test_temporal_expr_qualifies_created_at():
    src = open(os.path.join(os.path.dirname(__file__), os.pardir,
                            "plugins", "mnemo-pg2", "engine.py"),
                encoding="utf-8").read()
    assert "GREATEST(" in src
    # the temporal expression must not reference a bare `created_at`
    for frag in src.split("GREATEST(")[1:]:
        inner = frag.split(")", 1)[0]
        assert " - created_at" not in inner, "bare created_at in temporal expr"
