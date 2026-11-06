"""mnemo-pg2 fact extraction (vendor extract_and_store_facts parity,
mnemosyne/core/beam.py:8806 — pure-regex, deterministic, zero-model lane).

Vendor runs a 9-type multilingual regex pass on stored content and
lands each hit as a typed annotation on the memory row. We mirror the
core type set (metric, date, version, entity, negation, decision,
sequence, timeline). Vendor's optional LLM extraction tiers are
model-not-loaded on any headless deployment (their own documented
fail-soft path), so the regex lane IS the deployed behaviour we match.

append-only annotations (AnnotationStore.add_many semantics), cap 5
facts per memory (vendor parity: 0-5 fact strings).
"""
from __future__ import annotations

import re

_CAP = 5

_RES = [
    ("metric", re.compile(
        r"(\d+(?:[.,]\d+)?)\s*(ms|sec|seconds?|minutes?|hours?|days?|weeks?" +
        r"|months?|years?|kb|mb|gb|tb|percent|%)", re.IGNORECASE)),
    ("date", re.compile(
        r"\b(\d{4}-\d{2}-\d{2}|\d{1,2}\s+(jan|feb|mar|apr|may|jun|jul|aug" +
        r"|sep|sept|oct|nov|dec)[a-z]*\.?,?\s+\d{4}|\b(today|yesterday" +
        r"|tomorrow|monday|tuesday|wednesday|thursday|friday|saturday" +
        r"|sunday)\b)", re.IGNORECASE)),
    ("version", re.compile(r"\bv?(\d+\.\d+(?:\.\d+)?)\b")),
    ("entity", re.compile(r"\b([A-Z][a-zA-Z0-9]*(?:\s+[A-Z][a-zA-Z0-9]*){0,3})\b")),
    ("negation", re.compile(
        r"\b(\b(?:never|don'?t|doesn'?t|didn'?t|cannot|can'?t|shouldn'?t" +
        r"|won'?t|avoid|forbid(den)?\b)\b[^.!]{0,60})", re.IGNORECASE)),
    ("decision", re.compile(
        r"\b(decided|agreed|chose|resolved|settled)\b[^.!]{0,80}",
        re.IGNORECASE)),
    ("sequence", re.compile(
        r"\b(first(ly)?|then|next|finally|after ?that)\b", re.IGNORECASE)),
    ("timeline", re.compile(
        r"\b(since|until|by|before|after|from)\s+(the\s+)?\w+", re.IGNORECASE)),
]
_STOP = {"The", "I", "It", "This", "That", "He", "She", "They", "We",
         "Yesterday", "Today", "Tomorrow"}  # capital-word noise filter


def extract_facts(text: str) -> list[tuple[str, str]]:
    """[(fact_type, value)] capped at 5, deduped, deterministic order."""
    out: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    if not text:
        return out
    t = text[:8000]
    for kind, rx in _RES:
        for m in rx.finditer(t):
            val = (m.group(0) if kind != "date" or m.lastindex == 1
                   else m.group(1)).strip()
            if kind == "entity":
                val = m.group(1)
                if val in _STOP or len(val) < 3:
                    continue
            val = val[:160]
            key = (kind, val.lower())
            if key in seen:
                continue
            seen.add(key)
            out.append((kind, val))
            if len(out) >= _CAP:
                return out
    return out
