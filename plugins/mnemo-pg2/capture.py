"""P4-B auto-capture lane (vendor sync_turn parity, PG-native).

Two jobs, both mirrored from vendor
hermes_memory_provider/__init__.py (mnemosyne-oss @6aefa4f):

1. Machine-envelope sanitizer — Hermes injects envelope prefixes into the
   user message (cron preambles/replies, MCP reloads, watch notices,
   background-process completions, interrupted-turn boundaries, system
   notes). Vendor refuses to store pure notices (None) and strips notice
   prefixes before storing the genuine tail. Regexes ported VERBATIM.

2. Turn ledger idempotency (vendor's verbatim_ledger tickets) — a retried
   or replayed turn must not double-store: sha256(session|user|assistant)
   INSERT ... ON CONFLICT DO NOTHING; rowcount 0 = duplicate turn, skip.

Policy/role/size gates live in the caller (memory_provider.sync_turn);
everything here is pure + never raises into the host turn loop (callers
still wrap).
"""
from __future__ import annotations

import hashlib
import re
from typing import Optional

# --- vendor regexes, verbatim (file above; line refs in commit msg) ------
_INTERRUPTED_BOUNDARY = re.compile(
    r"^\[System\s+note:\s+(?:Your|The)\s+previous\s+turn\s+was\s+interrupted"
    r"[^\]]*?The\s+interrupted\s+request\s+was:\]\s*", re.IGNORECASE)
_CRON_HEADER = re.compile(
    r"^\[IMPORTANT:\s+You\s+are\s+running\s+as\s+a\s+scheduled\s+cron\s+job"
    r"\b[^\]]*\]\s*", re.IGNORECASE)
_CRON_REPLY = re.compile(
    r"^\[Replying\s+to:\s+\"Cronjob\s+Response:[^\"]*\"\s*\]\s*",
    re.IGNORECASE)
_MCP_RELOAD = re.compile(
    r"^\[IMPORTANT:\s+MCP\s+servers\s+have\s+been\s+reloaded\b[^\]]*\]\s*",
    re.IGNORECASE)
_WATCH_STATUS = re.compile(
    r"^\[IMPORTANT:\s+(?:Watch\s+pattern\s+for\s+process\s+[\w.-]+\s+disabled"
    r"|Watch-pattern\s+overflow:|Watch-pattern\s+notifications\s+resumed\.)"
    r"[^\]]*\]\s*", re.IGNORECASE)
_MULTI_PROCESS = re.compile(
    r"^\[IMPORTANT:\s+\d+\s+background\s+processes?\s+completed\b.*?\]\s*$",
    re.IGNORECASE | re.DOTALL)
_AUTHENTIC_NOTES = (
    re.compile(r"^\[System\s+note:\s+The\s+following\s+is\s+recalled\s+memory"
               r"\s+context[^\]]*\]\s*", re.IGNORECASE),
    re.compile(r"^\[System\s+note:\s+This\s+is\s+the\s+user's\s+very\s+first"
               r"\s+message\s+ever[^\]]*\]\s*", re.IGNORECASE),
    re.compile(r"^\[System\s+note:\s+This\s+\w+\s+had\s+an\s+earlier\s+Hermes"
               r"\s+session[^\]]*\]\s*", re.IGNORECASE),
)


def sanitize(text: Optional[str]) -> Optional[str]:
    """Vendor _sanitize_hermes_machine_envelope semantics:
    None  -> purely a machine notice (skip autosave)
    str   -> cleaned human content (may equal input)."""
    t = (text or "").strip()
    if not t:
        return ""
    m = _INTERRUPTED_BOUNDARY.match(t)
    if m:
        tail = t[m.end():].strip()
        if tail:
            return sanitize(tail)
    changed = True
    while changed:
        changed = False
        for pat in (_CRON_HEADER, _CRON_REPLY, _MCP_RELOAD, _WATCH_STATUS,
                    _MULTI_PROCESS, *_AUTHENTIC_NOTES):
            m = pat.match(t)
            if m:
                t = t[m.end():].strip()
                changed = True
                break
    if not t:
        return None
    return t


def turn_hash(session_id: str, user: str, assistant: str) -> str:
    h = hashlib.sha256()
    h.update((session_id or "").encode("utf-8", "replace"))
    h.update(b"\x00")
    h.update((user or "").encode("utf-8", "replace"))
    h.update(b"\x00")
    h.update((assistant or "").encode("utf-8", "replace"))
    return h.hexdigest()


LEDGER_DDL = (
    "CREATE TABLE IF NOT EXISTS turn_ledger ("
    "session_id TEXT NOT NULL, turn_hash TEXT NOT NULL,"
    " created_at TIMESTAMPTZ NOT NULL DEFAULT now(),"
    " PRIMARY KEY (session_id, turn_hash))")


def new_turn(cx, session_id: str, the_hash: str) -> bool:
    """True when this (session,turn) was not seen before (row inserted).
    Uses RETURNING so it works identically under READ COMMITTED."""
    rows = cx.query_dicts(
        "INSERT INTO turn_ledger(session_id, turn_hash) VALUES (%s, %s)"
        " ON CONFLICT DO NOTHING RETURNING turn_hash",
        (session_id or "", the_hash))
    if rows:
        return True
    # conflict path still owes the retention sweep occasionally; cheap +
    # bounded: drop tickets older than 30 days, at most per call here.
    cx.execute("DELETE FROM turn_ledger WHERE created_at < now() - "
               "interval '30 days'")
    return False
