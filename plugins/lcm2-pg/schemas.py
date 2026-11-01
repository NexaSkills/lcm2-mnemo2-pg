"""lcm2-pg tool schemas — the input contracts the model sees (F1).

Mirrors the vendor 15 tool schemas (hermes-lcm schemas.py: LCM_GREP,
LCM_RECALL, ... one dict per tool with name/description/parameters).
Descriptions here state the lcm2-pg reality: PG-native storage, per-agent
schema lcm2_<agent> in lcm2db, no lcm.db, no embedded database. Parameter surfaces
match tools.py (unknown params are ignored by handlers, so mirroring the
vendor is safe and honest).

get_tool_schemas() on the engine returns these in sorted name order;
plugin_entry registers the same dicts via ctx.register_tool so the
standalone-plugin path and the context-engine path expose identical
surfaces (the host dedups by name).
"""
from __future__ import annotations

_VARIANT = (
    " Storage is PostgreSQL-native: schema lcm2_<agent> in lcm2db on the "
    "lcm2-mnemo2-pg instance; there is no lcm.db and no embedded database."
)

LCM_GREP = {
    "name": "lcm_grep",
    "description": (
        "Search this agent's LCM2 database for past conversation content. "
        "Default scope is the active session: raw messages plus summary nodes "
        "at every DAG depth, fused full-text (Postgres FTS) and recency. "
        "Broader scopes ('all' = every session in this agent's schema, "
        "'session' = explicit session_id) return raw-message hits with exact "
        "store_id handles for lcm_expand drill-down." + _VARIANT
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": (
                    "Full-text query. Postgres websearch semantics: bare words are ANDed, "
                    "quotes make a phrase, words OR/NOT/and prefixes work. Prefer 1-3 "
                    "distinctive terms or one quoted phrase."
                ),
            },
            "limit": {
                "type": "integer",
                "description": "Max results (default 10).",
                "default": 10,
            },
            "session_scope": {
                "type": "string",
                "enum": ["current", "all", "session"],
                "description": (
                    "'current' (default) = active session; 'all' = every session in this "
                    "agent's schema; 'session' = the session_id supplied below."
                ),
                "default": "current",
            },
            "session_id": {
                "type": "string",
                "description": "Explicit session id, required with session_scope='session'.",
            },
            "role": {
                "type": "string",
                "enum": ["system", "user", "assistant", "tool", "unknown"],
                "description": "Optional raw-message role filter.",
            },
            "time_from": {
                "description": "Optional inclusive minimum timestamp (Unix seconds or ISO 8601 with timezone).",
            },
            "time_to": {
                "description": "Optional inclusive maximum timestamp (Unix seconds or ISO 8601 with timezone).",
            },
        },
        "required": ["query"],
    },
}

LCM_RECALL = {
    "name": "lcm_recall",
    "description": (
        "Search this agent's entire LCM2 memory across all sessions by meaning: "
        "summary nodes plus verbatim message excerpts ranked by full-text "
        "strength, recency, and connection strength (edge relatedness). Returns "
        "snippets with exact handles (store_id / node_id) for drill-down via "
        "lcm_expand. For exact wording in a known session/time range, use "
        "lcm_grep." + _VARIANT
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Natural-language description of what to recall."},
            "limit": {"type": "integer", "description": "Max memories (default 8).", "default": 8},
            "include": {
                "type": "string",
                "enum": ["all", "summaries", "verbatim"],
                "description": "Which memory kinds to return (default 'all').",
                "default": "all",
            },
            "session_scope": {
                "type": "string",
                "enum": ["all", "current"],
                "description": "'all' (default) spans sessions; 'current' restricts to the active session.",
                "default": "all",
            },
        },
        "required": ["query"],
    },
}

LCM_RECENT = {
    "name": "lcm_recent",
    "description": (
        "Summaries of the recent past in natural UTC periods: today, yesterday, "
        "7d, week, month, date:YYYY-MM-DD, last 6h. Returns leaf summaries in the "
        "window for this agent's sessions." + _VARIANT
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "period": {"type": "string", "description": "UTC period token (today | yesterday | Nd | week | month | date:YYYY-MM-DD | last Nh)."},
            "limit": {"type": "integer", "description": "Max sections (default 10).", "default": 10},
        },
        "required": ["period"],
    },
}

LCM_LOAD_SESSION = {
    "name": "lcm_load_session",
    "description": (
        "Ordered raw-message transcript page for one explicit session_id in "
        "this agent's schema (enumeration, not search; pagination via "
        "after_store_id; roles/time bounds optional)." + _VARIANT
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "session_id": {"type": "string", "description": "Explicit session id to load."},
            "limit": {"type": "integer", "description": "Max messages (default 100).", "default": 100},
            "after_store_id": {"type": "integer", "description": "Exclusive store_id cursor for pagination.", "default": 0},
            "roles": {"type": "array", "items": {"type": "string"}, "description": "Optional role filter."},
            "time_from": {"description": "Optional inclusive minimum timestamp."},
            "time_to": {"description": "Optional inclusive maximum timestamp."},
        },
        "required": ["session_id"],
    },
}

LCM_EXPAND = {
    "name": "lcm_expand",
    "description": (
        "Recover original detail behind a summary node (node_id — what source "
        "messages / lower nodes were compacted into it) or a single raw "
        "message (store_id). Pageable via content_offset/source_offset; output "
        "bounded by max_tokens." + _VARIANT
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "node_id": {"type": "integer", "description": "Summary node id to expand."},
            "store_id": {"type": "integer", "description": "Single raw message by store_id."},
            "max_tokens": {"type": "integer", "description": "Token budget for returned content (default 4000).", "default": 4000},
            "content_offset": {"type": "integer", "description": "Continuation offset for oversized content.", "default": 0},
        },
    },
}

LCM_DESCRIBE = {
    "name": "lcm_describe",
    "description": (
        "Inspect one summary node's subtree metadata (token counts, child "
        "manifest, expand hints) without loading its content, or the session's "
        "top-level DAG overview when node_id is omitted." + _VARIANT
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "node_id": {"type": "integer", "description": "Summary node id to inspect (omit for session overview)."},
        },
    },
}

LCM_EXPAND_QUERY = {
    "name": "lcm_expand_query",
    "description": (
        "Answer a question from expanded LCM2 context: finds candidate "
        "summaries/raw matches for the query (or explicit node_ids), expands "
        "them under a context budget, and returns a bounded synthesis. Model "
        "calls are DISABLED in this build — the tool returns the assembled "
        "evidence window instead of prose." + _VARIANT
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "prompt": {"type": "string", "description": "The question or task to answer from expanded context."},
            "query": {"type": "string", "description": "Search query used to find candidates before expansion."},
            "node_ids": {"type": "array", "items": {"type": "integer"}, "description": "Explicit node ids to expand instead of searching."},
            "max_results": {"type": "integer", "description": "Max candidates to expand (default 5).", "default": 5},
            "max_tokens": {"type": "integer", "description": "Bounded synthesis token budget (default 2000).", "default": 2000},
        },
        "required": ["prompt"],
    },
}

LCM_QUERY_STATE = {
    "name": "lcm_query_state",
    "description": (
        "Bounded, typed, source-cited assertions for a canonical subject "
        "(e.g. user:self, project:x): facts, preferences, commitments, "
        "actions, status, quotations. Every row carries an exact message "
        "store_id span + quote; conflicting state is preserved, not resolved "
        "by recency. Status disabled when the assertion sidecar is off."
        + _VARIANT
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "subject_key": {"type": "string", "description": "Canonical subject, e.g. 'user:self'."},
            "predicate_key": {"type": "string", "description": "Optional dotted predicate filter."},
            "kinds": {"type": "array", "items": {"type": "string"}, "description": "Optional assertion-kind filter."},
            "scope_key": {"type": "string", "description": "Optional exact scope filter; empty means unscoped."},
            "as_of": {"description": "Optional historical boundary (Unix seconds or timezone-aware ISO 8601)."},
            "limit": {"type": "integer", "description": "Max rows (default 25).", "default": 25},
        },
        "required": ["subject_key"],
    },
}

LCM_COMPUTE = {
    "name": "lcm_compute",
    "description": (
        "Deterministic date/count/order/latest-state operation over exact "
        "cited rows. Supply only exact message spans (store_id + span offsets "
        "+ quote) whose values and dates appear verbatim in the cited "
        "evidence. Unsupported or conflicting inputs fail closed to an "
        "evidence-only answer." + _VARIANT
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "question": {"type": "string", "description": "The question, unchanged."},
            "operands": {
                "type": "array",
                "minItems": 1,
                "maxItems": 50,
                "items": {
                    "type": "object",
                    "properties": {
                        "store_id": {"type": "integer"},
                        "span_start": {"type": "integer"},
                        "span_end": {"type": "integer"},
                        "quote": {"type": "string"},
                        "value": {"description": "Explicit operand value."},
                        "unit": {"type": "string"},
                        "key": {"type": "string", "description": "Canonical distinct-event/entity key."},
                        "label": {"type": "string"},
                        "date": {"type": "string"},
                    },
                    "required": ["quote"],
                },
            },
        },
        "required": ["question", "operands"],
    },
}

LCM_COMPILE_EVIDENCE = {
    "name": "lcm_compile_evidence",
    "description": (
        "Compile a bounded evidence brief from exact baseline refs: validates "
        "every quote/span against stored rows, reports a state "
        "(partial | answer-sufficient | finite-coverage | conflicted | "
        "unknown), never prose." + _VARIANT
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "question": {"type": "string", "description": "The question."},
            "baseline_refs": {
                "type": "array",
                "minItems": 1,
                "maxItems": 50,
                "items": {
                    "type": "object",
                    "properties": {
                        "store_id": {"type": "integer"},
                        "span_start": {"type": "integer"},
                        "span_end": {"type": "integer"},
                        "quote": {"type": "string"},
                    },
                },
            },
            "question_date": {"type": "string", "description": "Optional calendar-date anchor."},
        },
        "required": ["question", "baseline_refs"],
    },
}

LCM_EVIDENCE_PACK = {
    "name": "lcm_evidence_pack",
    "description": (
        "Lower-level exact-ref evidence packet: hydrates exact spans, "
        "deduplicates refs, keeps occurrence time distinct from observation "
        "time, and emits a canonical compute trace only when grounding "
        "closes. Returns no prose." + _VARIANT
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "question": {"type": "string", "description": "The question."},
            "baseline_refs": {
                "type": "array",
                "minItems": 1,
                "maxItems": 50,
                "items": {
                    "type": "object",
                    "properties": {
                        "store_id": {"type": "integer"},
                        "span_start": {"type": "integer"},
                        "span_end": {"type": "integer"},
                        "quote": {"type": "string"},
                    },
                },
            },
            "question_date": {"type": "string", "description": "Optional calendar-date anchor."},
        },
        "required": ["question", "baseline_refs"],
    },
}

LCM_RETRIEVE = {
    "name": "lcm_retrieve",
    "description": (
        "Bounded evidence-retrieval episode: start with typed intent and "
        "named evidence slots, run up to three targeted retrieval steps "
        "(search actions are executed against this agent's store), finish "
        "with exact selected refs." + _VARIANT
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["start", "search", "finish", "status", "abandon"],
                "description": "Episode action.",
            },
            "retrieval_id": {"type": "string", "description": "Id returned by action='start'."},
            "question": {"type": "string", "description": "The question, unchanged."},
            "identity": {
                "type": "object",
                "description": "Typed intent (intent_type + optional subject/operation/time keys).",
                "properties": {"intent_type": {"type": "string"}},
                "required": ["intent_type"],
            },
            "requirements": {
                "type": "array",
                "maxItems": 12,
                "items": {
                    "type": "object",
                    "properties": {
                        "slot_id": {"type": "string"},
                        "description": {"type": "string"},
                        "minimum_refs": {"type": "integer", "default": 1},
                    },
                    "required": ["slot_id", "description"],
                },
            },
            "resolved_slots": {"type": "array", "maxItems": 12, "items": {"type": "object"}},
            "selected_refs": {"type": "array", "maxItems": 40, "items": {"type": "string"}},
        },
        "required": ["action"],
    },
}

LCM_STATUS = {
    "name": "lcm_status",
    "description": (
        "Quick health overview of the LCM2 engine for this agent: message "
        "count, sessions, summary nodes, DAG depth distribution, compaction "
        "and ingestion counters." + _VARIANT
    ),
    "parameters": {"type": "object", "properties": {}},
}

LCM_INSPECT = {
    "name": "lcm_inspect",
    "description": (
        "Read-only metadata inventory: session/conversation lineage, message "
        "frontier, DAG compaction frontier, externalized payload refs, "
        "fresh-tail counts." + _VARIANT
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "limit": {"type": "integer", "description": "Max rows per section (default 20).", "default": 20},
        },
    },
}

LCM_DOCTOR = {
    "name": "lcm_doctor",
    "description": (
        "Diagnostics: schema/table presence, connection health, FTS index "
        "presence, per-table row counts, corruption checks (no memory "
        "content)." + _VARIANT
    ),
    "parameters": {"type": "object", "properties": {}},
}

TOOLS = {
    s["name"]: s
    for s in (
        LCM_GREP, LCM_RECALL, LCM_RECENT, LCM_LOAD_SESSION, LCM_EXPAND,
        LCM_DESCRIBE, LCM_EXPAND_QUERY, LCM_QUERY_STATE, LCM_COMPUTE,
        LCM_COMPILE_EVIDENCE, LCM_EVIDENCE_PACK, LCM_RETRIEVE, LCM_STATUS,
        LCM_INSPECT, LCM_DOCTOR,
    )
}
