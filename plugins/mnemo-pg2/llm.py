"""mnemo-pg2 LLM lane for consolidation summaries.

Fleet rule: memory LLM calls go ONLY to the operator's LiteLLM proxy via
the `aux` alias — stdlib urllib, no new deps, fail-soft to None (the
caller keeps its no-LLM fallback; consolidation must never depend on the
model being reachable).

Env knobs (all optional):
  MNEMO2_LLM_URL    full OpenAI-compat base (default: proxy below + /v1)
  MNEMO2_LLM_MODEL  model/alias name   (default "aux")
  MNEMO2_LLM_KEY    key override       (default: the profile's proxy key)
"""
from __future__ import annotations

import json
import os
import urllib.request

DEFAULT_URL = "https://ai.nexaskills.co.za/v1"
DEFAULT_MODEL = "main"  # verified live: the beez keys' teams allow ONLY
                       # 'main' (403 team_model_access_denied on 'aux');
                       # override with MNEMO2_LLM_MODEL where an aux
                       # alias exists.
KEY_ENV_ORDER = ("MNEMO2_LLM_KEY",
                 "HERMES_CUSTOM_AI_NEXASKILLS_CO_ZA_API_KEY")

_SYSTEM = ("You compress agent conversation memories into one dense "
           "summary. Keep every durable fact, name, number, decision and "
           "preference. No preamble, no commentary. Under 120 words.")


def _key() -> str:
    for name in KEY_ENV_ORDER:
        v = os.environ.get(name, "")
        if v:
            return v
    return ""


def summarize(text: str, *, timeout: float = 12.0) -> str | None:
    """One chat-completion call; None on ANY failure (never raises)."""
    key = _key()
    if not (text or "").strip() or not key:
        return None
    url = os.environ.get("MNEMO2_LLM_URL", "").strip() or DEFAULT_URL
    model = os.environ.get("MNEMO2_LLM_MODEL", "").strip() or DEFAULT_MODEL
    body = json.dumps({
        "model": model,
        "max_tokens": 300,
        "temperature": 0.2,
        "messages": [{"role": "system", "content": _SYSTEM},
                     {"role": "user", "content": text[:12000]}],
    }).encode("utf-8")
    req = urllib.request.Request(
        url.rstrip("/") + "/chat/completions", data=body, method="POST",
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {key}"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read().decode("utf-8", "replace"))
        content = data["choices"][0]["message"]["content"]
        content = (content or "").strip()
        return content or None
    except Exception:
        return None
