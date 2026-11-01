"""lcm2-pg engine: native-PG lossless context engine (compression core).

Every message is persisted to the agent's private schema (lcm2_<agent>);
past a token threshold the engine builds a summary DAG (leaf summaries over
message batches, then higher depths) — messages are NEVER deleted (lossless).
Summaries use the host router model when available; LCM2_DISABLE_MODEL=1
forces deterministic extractive summaries (tests / degraded mode).
"""
from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timezone

import pgconn

logger = logging.getLogger("lcm2-pg")

DEFAULT_THRESHOLD = 0.75
LEAF_CHUNK_TOKENS = int(os.environ.get("LCM2_LEAF_CHUNK_TOKENS", "800"))
FRESH_TAIL = int(os.environ.get("LCM2_FRESH_TAIL_COUNT", "8"))


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _est_tokens(text: str) -> int:
    return max(1, len(text or "") // 4)


def _extractive_summary(items: list[tuple[str, str]]) -> str:
    """Deterministic fallback summary: role-labelled leading fragments."""
    lines = []
    for role, content in items:
        frag = (content or "").strip().replace("\n", " ")
        if len(frag) > 140:
            frag = frag[:137] + "..."
        lines.append(f"- {role}: {frag}")
    return "\n".join(lines)


def _model_summary(items: list[tuple[str, str]]) -> str | None:
    if os.environ.get("LCM2_DISABLE_MODEL") == "1":
        return None
    try:  # host-side router when running inside Hermes
        from agent.auxiliary_client import call_text  # type: ignore
        transcript = "\n".join(f"{r}: {(c or '')[:400]}" for r, c in items)
        out = call_text(
            "Summarize this conversation fragment in <=3 bullet lines, "
            "preserving names, numbers and decisions:\n" + transcript)
        return (out or "").strip() or None
    except Exception:
        return None


class LCM2Engine:
    """Context engine bound to one profile: schema lcm2_<agent> on lcm2db."""

    def __init__(self, dsn: str | None = None, schema: str | None = None):
        self.dsn = dsn or pgconn.default_dsn()
        self.schema = schema or pgconn.agent_schema()
        self._bootstrapped = False
        with self._conn(ddl=True) as cx:
            cx.bootstrap()
        self._bootstrapped = True

    # -- connection -----------------------------------------------------
    def _conn(self, ddl: bool = False):
        return pgconn.connect(self.dsn, self.schema, ddl=ddl)

    def _threshold(self) -> float:
        env = os.environ.get("LCM_CONTEXT_THRESHOLD")
        if env is not None:
            try:
                return float(env)
            except ValueError:
                pass
        try:  # host config: lcm.context_threshold (yaml, read-only)
            from agent_config import get_lcm_context_threshold  # type: ignore
            v = get_lcm_context_threshold()
            if v is not None:
                return float(v)
        except Exception:
            pass
        return DEFAULT_THRESHOLD

    # -- ingestion ------------------------------------------------------
    def ingest(self, session_id: str, role: str, content: str, *,
               tool_calls=None, tool_name=None, tool_call_id=None,
               conversation_id: str = "", source: str = "") -> int:
        ts = _now()
        with self._conn() as cx:
            row = cx.query(
                "insert into messages(session_id, source, conversation_id, role,"
                " content, tool_call_id, tool_calls, tool_name, timestamp,"
                " token_estimate, ingested_at)"
                " values (%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s,%s)"
                " returning store_id",
                (session_id, source, conversation_id, role, content,
                 tool_call_id,
                 json.dumps(tool_calls) if tool_calls is not None else None,
                 tool_name, ts, _est_tokens(content), ts))
            self._touch_lifecycle(cx, session_id)
            return row[0][0]

    def _touch_lifecycle(self, cx, session_id: str) -> None:
        # literal TRUE::boolean in the VALUES keeps the singleton PK type
        # resolvable; the param appears once, cast explicitly ($1::text) —
        # an untyped param in both VALUES and DO UPDATE is IndeterminateDatatype
        cx.execute(
            "insert into lcm_lifecycle_state(singleton, state, updated_at)"
            " values (TRUE, jsonb_build_object('last_session', %s::text,"
            " 'at', now()), now())"
            " on conflict (singleton) do update"
            " set state = jsonb_set(lcm_lifecycle_state.state,"
            " '{last_session}', to_jsonb(excluded.state->>'last_session')),"
            " updated_at = now()",
            (session_id,))

    # -- compression ----------------------------------------------------
    def context_tokens(self, session_id: str) -> int:
        with self._conn() as cx:
            return cx.query_scalar(
                "select coalesce(sum(token_estimate),0) from messages"
                " where session_id=%s", (session_id,)) or 0

    def maybe_compress(self, session_id: str) -> dict:
        """Compress message batches that have no leaf summary yet."""
        with self._conn() as cx:
            covered = set()
            rows = cx.query(
                "select source_ids from summary_nodes"
                " where session_id=%s and source_type='messages'", (session_id,))
            for r in rows:
                covered.update(json.loads(r[0]))
            msgs = cx.query_dicts(
                "select store_id, role, content from messages"
                " where session_id=%s and pinned = false"
                " order by store_id", (session_id,))
        pending = [m for m in msgs if m["store_id"] not in covered]
        if not pending:
            return {"compressed": False, "reason": "nothing uncovered",
                    "compressed_batches": 0}
        # keep a fresh tail uncompressed (recent turns stay verbatim)
        body = pending[:-FRESH_TAIL] if len(pending) > FRESH_TAIL + 4 else pending
        if not body:
            return {"compressed": False, "reason": "fresh tail only",
                    "compressed_batches": 0}
        batches, batch = [], []
        batch_tokens = 0
        for m in body:
            batch.append(m)
            batch_tokens += _est_tokens(m["content"] or "")
            if batch_tokens >= LEAF_CHUNK_TOKENS:
                batches.append(batch)
                batch, batch_tokens = [], 0
        if batch:
            batches.append(batch)
        made = 0
        ts = _now()
        for batch in batches:
            items = [(m["role"], m["content"] or "") for m in batch]
            summary = _model_summary(items) or _extractive_summary(items)
            ids = [m["store_id"] for m in batch]
            src_tokens = sum(_est_tokens(m["content"] or "") for m in batch)
            with self._conn() as cx:
                cx.execute(
                    "insert into summary_nodes(session_id, depth, summary,"
                    " token_count, source_token_count, source_ids, source_type,"
                    " created_at)"
                    " values (%s,0,%s,%s,%s,%s::jsonb,'messages',%s)",
                    (session_id, summary, _est_tokens(summary), src_tokens,
                     json.dumps(ids), ts))
                cx.execute(
                    "insert into lcm_rollup_invalidations(session_id, reason)"
                    " values (%s, 'leaf-summary')", (session_id,))
            made += 1
        rolled = self.maybe_rollup(session_id)
        return {"compressed": made > 0, "compressed_batches": made,
                "rolled_up": rolled, "uncovered_messages": 0 if made else len(pending)}

    def maybe_rollup(self, session_id: str, fanin: int = 4) -> bool:
        """Fold fanin leaf summaries into one depth-1 node (DAG upward)."""
        with self._conn() as cx:
            leaves = cx.query_dicts(
                "select node_id, summary, token_count, source_ids"
                " from summary_nodes where session_id=%s and depth=0"
                " order by node_id", (session_id,))
            existing = cx.query_scalar(
                "select count(*) from summary_nodes"
                " where session_id=%s and depth=1", (session_id,))
            fresh = leaves[existing * fanin:] if existing else leaves
            if len(fresh) < fanin:
                return False
            group = fresh[:fanin]
            combined = "\n".join(f"[node {g['node_id']}] {g['summary']}"
                                 for g in group)
            summary = _model_summary([("summary", combined)]) \
                or _extractive_summary([("summary", combined)])
            src = [g["node_id"] for g in group]
            cx.execute(
                "insert into summary_nodes(session_id, depth, summary,"
                " token_count, source_token_count, source_ids, source_type,"
                " created_at)"
                " values (%s,1,%s,%s,%s,%s::jsonb,'summaries',now())",
                (session_id, summary, _est_tokens(summary),
                 sum(g["token_count"] for g in group), json.dumps(src)))
            return True

    # -- assembly (what the host sees) -----------------------------------
    def assemble(self, session_id: str) -> dict:
        with self._conn() as cx:
            total = cx.query_scalar(
                "select coalesce(sum(token_estimate),0) from messages"
                " where session_id=%s", (session_id,)) or 0
            nodes = cx.query_dicts(
                "select node_id, depth, summary, token_count from summary_nodes"
                " where session_id=%s order by depth, node_id", (session_id,))
            tail = cx.query_dicts(
                "select role, content from messages where session_id=%s"
                " order by store_id desc limit %s", (session_id, FRESH_TAIL))
        return {"tokens_total": total,
                "context_window_pressure": round(
                    (total or 1) / max(1, int(os.environ.get(
                        "LCM2_CONTEXT_WINDOW_TOKENS", "128000"))), 4),
                "summaries": nodes, "fresh_tail": list(reversed(tail))}

    # the tool surface lives in tools.py; thin delegates here so tests and
    # register() hit the same callables:
    def tools_lcm_grep(self, args):
        import tools
        return tools.lcm_grep(args, engine=self)

    def tools_lcm_expand(self, args):
        import tools
        return tools.lcm_expand(args, engine=self)
