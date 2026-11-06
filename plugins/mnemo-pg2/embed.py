"""mnemo-pg2 embedding providers — pure Python + local fastembed ONNX, zero
sqlite, never the main/active LLM (Phase 5 spec).

Providers (selected by MNEMO2_EMBED_MODEL; "" = auto):
  fastembed[:model]  local ONNX via fastembed (vendor parity; default model
                     BAAI/bge-small-en-v1.5, 384-dim; cache dir
                     MNEMO2_EMBED_CACHE, default <repo>/models/fastembed)
  hashed             deterministic feature-hash, 256-dim, zero deps (fallback)
  api                OpenAI-compatible POST when MNEMO2_EMBED_API_URL is set
Auto order: fastembed if importable AND loadable, else hashed. A load failure
downgrades the process to hashed permanently (logged once) — never raises.

HARD rule: this module imports stdlib + fastembed ONLY. It never imports
llm.py and never reads proxy/LLM env (guarded by test_s5).
"""
from __future__ import annotations

import hashlib
import math
import os
import re
import threading
import unicodedata

_WORDS = re.compile(r"[a-z0-9]+")
_DEFAULT_FASTEMBED_MODEL = "BAAI/bge-small-en-v1.5"

_state = {"provider": None, "model": None, "fail": False, "lock":
          threading.Lock()}
_fe_model = None  # loaded fastembed TextEmbedding singleton


def _repo_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))))


def _cache_dir() -> str:
    return os.environ.get(
        "MNEMO2_EMBED_CACHE",
        os.path.join(_repo_root(), "models", "fastembed"))


def _hashed(text: str, dim: int | None = None) -> list[float]:
    dim = dim or 256
    t = unicodedata.normalize("NFKD", (text or "").lower())
    t = "".join(c for c in t if not unicodedata.combining(c))
    ws = _WORDS.findall(t)
    toks = list(ws)
    joined = "".join(ws)
    toks += [joined[i:i + 3] for i in range(max(len(joined) - 2, 0))]
    v = [0.0] * dim
    for tok in toks:
        h = hashlib.blake2b(tok.encode(), digest_size=8).digest()
        idx = int.from_bytes(h[:4], "big") % dim
        sign = 1.0 if h[4] & 1 else -1.0
        v[idx] += sign
    n = math.sqrt(sum(x * x for x in v))
    return [x / n for x in v] if n > 0 else v


def _configured() -> tuple[str, str]:
    """(provider, model) from env per spec precedence."""
    url = os.environ.get("MNEMO2_EMBED_API_URL")
    raw = (os.environ.get("MNEMO2_EMBED_MODEL") or "").strip()
    if raw == "hashed":
        return "hashed", "hashed256"
    if raw == "api" or (url and not raw):
        return "api", raw or "api"
    if raw.startswith("fastembed"):
        model = raw.split(":", 1)[1] if ":" in raw else (
            _DEFAULT_FASTEMBED_MODEL)
        return "fastembed", model
    if raw and url:  # explicit model + url = API model
        return "api", raw
    return "fastembed", _DEFAULT_FASTEMBED_MODEL  # auto


def _get_fe():
    """Load the fastembed singleton once; None after a permanent failure."""
    global _fe_model
    if _fe_model is not None or _state["fail"]:
        return _fe_model
    with _state["lock"]:
        if _fe_model is not None or _state["fail"]:
            return _fe_model
        try:
            from fastembed import TextEmbedding
            _fe_model = TextEmbedding(
                model_name=_configured()[1], cache_dir=_cache_dir())
        except Exception:
            _state["fail"] = True
            _fe_model = None
    return _fe_model


def warm() -> bool:
    """Warm the model off the turn path (provider boot thread). True =
    fastembed usable in this process."""
    return _get_fe() is not None


def active_provider() -> str:
    p, _m = _configured()
    if p == "fastembed" and _get_fe() is None:
        return "hashed"  # permanent fail-soft downgrade
    return p


def active_model() -> str:
    p, m = _configured()
    if p == "fastembed" and _get_fe() is None:
        return "hashed256"
    return m if p != "fastembed" else f"fastembed:{m}"


def active_dim() -> int:
    p = active_provider()
    if p == "fastembed":
        return 384  # bge-small family; other models via MNEMO2_EMBED_DIM
    if p == "hashed":
        return int(os.environ.get("MNEMO2_EMBED_DIM", "256"))
    return int(os.environ.get("MNEMO2_EMBED_DIM", "0")) or 0


def _fe_encode(texts: list[str], prefix: str) -> list[list[float]] | None:
    fe = _get_fe()
    if fe is None:
        return None
    try:
        return [list(map(float, v)) for v in
                fe.embed([(prefix + t) if prefix else (t or "")
                          for t in texts])]
    except Exception:
        _state["fail"] = True  # runtime break = downgrade like load fail
        return None


def embed_text(text: str, dim: int | None = None) -> list[float]:
    """DOCUMENT lane (write side). Never raises."""
    p, _m = _configured()
    if p == "api" or (os.environ.get("MNEMO2_EMBED_API_URL") and p != "hashed"):
        try:
            return _embed_api(text or "", dim)
        except Exception:
            p = "hashed"
    if p == "fastembed":
        got = _fe_encode([text], os.environ.get("MNEMO2_EMBED_DOC_PREFIX", ""))
        if got:
            return got[0]
    return _hashed(text, dim or (256 if active_provider() == "hashed"
                                 else None))


def embed_query(text: str) -> list[float]:
    """QUERY lane (recall side). Same provider, query prefix."""
    p, _m = _configured()
    if p == "fastembed":
        got = _fe_encode([text], os.environ.get("MNEMO2_EMBED_QUERY_PREFIX",
                                                ""))
        if got:
            return got[0]
    return embed_text(text)


def embed_docs(texts: list[str]) -> list[list[float]] | None:
    """Batched document encode (migration/backfill). None = provider
    unavailable; caller falls back to per-text embed_text()."""
    p, _m = _configured()
    if p == "fastembed":
        return _fe_encode(list(texts),
                          os.environ.get("MNEMO2_EMBED_DOC_PREFIX", ""))
    if p == "hashed":
        return [_hashed(t) for t in texts]
    return None


def _embed_api(text: str, dim: int | None) -> list[float]:
    import json
    import urllib.request

    url = os.environ.get("MNEMO2_EMBED_API_URL", "")
    payload = {"input": [text], "model": _configured()[1]}
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    key = os.environ.get("MNEMO2_EMBED_API_KEY")
    if key:
        req.add_header("Authorization", f"Bearer {key}")
    with urllib.request.urlopen(req, timeout=30) as r:
        data = json.loads(r.read().decode())
    return list(data["data"][0]["embedding"])


def cosine(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


def to_pg_vector(vec: list[float]) -> str:
    return "[" + ",".join(f"{x:.6f}" for x in vec) + "]"
