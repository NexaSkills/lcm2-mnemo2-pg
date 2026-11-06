"""S5: semantic embeddings (fastembed lane, PG-native, never the LLM).

Covers: (1) HARD no-LLM guard on the embed module; (2) the paraphrase
property that motivated S5 (fastembed finds it, hashed demonstrably does
not); (3) dim-guard fail-soft through the real write lane; (4) provider
model tags land in memory_embeddings.model.
"""
import os
import re
import sys
import pathlib

import pytest

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "plugins" / "mnemo-pg2"))

import plugin_entry as pe  # noqa: E402
import engine as _engine_mod  # noqa: E402
import embed as _embed_mod  # noqa: E402

_pg = _engine_mod.pgconn

DOC = "the payment gateway times out when the bank api is slow"
QUERY = "why does online checkout die whenever the banking service drags"


def call(tool, **args):
    import json
    return json.loads(pe._dispatch(tool, args))


@pytest.fixture
def fastembed_on(monkeypatch):
    monkeypatch.setenv("MNEMO2_EMBED_MODEL", "fastembed")
    monkeypatch.setattr(_embed_mod, "_fe_model", None)
    monkeypatch.setattr(_embed_mod, "_state", dict(
        _embed_mod._state, next_try=0.0, warned=False))
    if not _embed_mod.warm():
        pytest.skip("fastembed unavailable on this host")
    yield


def test_no_llm_in_embed_lane():
    src = (REPO / "plugins" / "mnemo-pg2" / "embed.py").read_text()
    assert not re.search(r"\bimport\s+llm\b|from\s+llm\b", src)
    assert not re.search(r"LITELLM|HERMES_.*API_KEY|ai\.nexaskills", src)
    imports = re.findall(r"^\s*(?:from|import)\s+([\w.]+)", src, re.M)
    for m in imports:
        root = m.split(".")[0]
        assert root in {
            "__future__", "hashlib", "math", "os", "re", "threading",
            "time", "unicodedata", "json", "urllib", "fastembed",
            "numpy", "logging"}, root


def test_paraphrase_semantic_pair(fastembed_on):
    v_doc = _embed_mod.embed_text(DOC)
    v_q = _embed_mod.embed_query(QUERY)
    assert len(v_doc) == 384 and len(v_q) == 384
    sem = _embed_mod.cosine(v_doc, v_q)
    os.environ["MNEMO2_EMBED_MODEL"] = "hashed"
    lex = _embed_mod.cosine(_embed_mod.embed_text(DOC),
                            _embed_mod.embed_query(QUERY))
    assert sem > 0.55, f"fastembed semantic sim {sem:.2f}"
    assert lex < 0.45, f"hashed unexpectedly caught the paraphrase: {lex:.2f}"


def _priv_schema():
    import pgconn
    with pgconn.connect(None) as cx:
        return cx.schema


def test_write_lane_model_tag_and_dims():
    import pgconn
    tag = call("mnemosyne_remember",
               content="S5TAG the alpine swift nests above the ninth ridge",
               importance=0.9)
    assert tag["ok"]
    schema = _priv_schema()
    with pgconn.connect(None) as cx:
        row = cx.query_dicts(
            f"SELECT vector_dims(embedding) AS d, model"
            f" FROM {schema}.memory_embeddings WHERE memory_id=%s",
            (tag["id"],))
    assert row and row[0]["d"] == 256  # suite runs pinned to hashed
    assert "hash" in (row[0]["model"] or "")
    with pgconn.connect(None) as cx:
        cx.execute(f"delete from {schema}.working_memory where id=%s",
                   (tag["id"],))
        cx.execute(f"delete from {schema}.memory_embeddings"
                   " where memory_id=%s", (tag["id"],))


def test_dim_guard_fail_soft(monkeypatch):
    """A provider whose dim disagrees with the column yields a NULL
    vector, never an error and never a corrupt row."""
    import pgconn
    monkeypatch.setattr(_embed_mod, "embed_text", lambda *a, **k: [0.1] * 999)
    monkeypatch.setattr(_embed_mod, "embed_query",
                        lambda *a, **k: [0.1] * 999)
    monkeypatch.setattr(_engine_mod._embed, "embed_text",
                        lambda *a, **k: [0.1] * 999)
    monkeypatch.setattr(_engine_mod._embed, "embed_query",
                        lambda *a, **k: [0.1] * 999)
    _engine_mod._VECDIM.clear()
    r = call("mnemosyne_remember", content="S5GUARD row with mismatched dim",
            importance=0.5)
    assert r["ok"]  # write succeeded
    schema = _priv_schema()
    with pgconn.connect(None) as cx:
        row = cx.query_dicts(
            f"SELECT embedding IS NULL AS nul"
            f" FROM {schema}.memory_embeddings WHERE memory_id=%s",
            (r["id"],))
        cx.execute(f"delete from {schema}.working_memory where id=%s",
                   (r["id"],))
        cx.execute(f"delete from {schema}.memory_embeddings"
                   " where memory_id=%s", (r["id"],))
    assert row and row[0]["nul"]


def test_recall_survives_null_query_vector(monkeypatch):
    """Query-side dim mismatch must degrade scores to 0, not raise."""
    monkeypatch.setattr(_engine_mod._embed, "embed_query",
                        lambda *a, **k: [0.1] * 999)
    _engine_mod._VECDIM.clear()
    r = call("mnemosyne_recall", query="anything at all here", limit=3)
    assert r["ok"]
