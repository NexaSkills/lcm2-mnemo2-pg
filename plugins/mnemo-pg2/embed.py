"""mnemo-pg2 embedding function — pure Python, zero sqlite, zero network by
default (spec §4).

Deterministic hashed embedding: content -> stable tokens (3-grams + words,
lowercased, ascii-folded) -> signed hash into VEC_DIM dims -> L2-normalise.
Same shape contract an API embedder would return (list[float], dim 256).
If MNEMO2_EMBED_API_URL is set, engine calls post to it (vendor EMBEDDING_*
family honoured); tests never set it (determinism).
"""
from __future__ import annotations

import hashlib
import math
import os
import re
import unicodedata

_WORDS = re.compile(r"[a-z0-9]+")


def _tokens(text: str) -> list[str]:
    t = unicodedata.normalize("NFKD", text.lower())
    t = "".join(c for c in t if not unicodedata.combining(c))
    ws = _WORDS.findall(t)
    toks = list(ws)
    joined = "".join(ws)
    toks += [joined[i:i + 3] for i in range(max(len(joined) - 2, 0))]
    return toks


def embed_text(text: str, dim: int | None = None) -> list[float]:
    url = os.environ.get("MNEMO2_EMBED_API_URL")
    if url:  # API path (same shape), never in tests
        return _embed_api(text or "", url, dim)
    dim = dim or int(os.environ.get("MNEMO2_EMBED_DIM", "256"))
    v = [0.0] * dim
    for tok in _tokens(text or ""):
        h = hashlib.blake2b(tok.encode(), digest_size=8).digest()
        idx = int.from_bytes(h[:4], "big") % dim
        sign = 1.0 if h[4] & 1 else -1.0
        v[idx] += sign
    n = math.sqrt(sum(x * x for x in v))
    if n > 0:
        v = [x / n for x in v]
    return v


def _embed_api(text: str, url: str, dim: int | None) -> list[float]:
    import json
    import urllib.request

    payload = {"input": [text], "model": os.environ.get("MNEMO2_EMBED_MODEL", "")}
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
