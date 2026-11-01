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

try:  # file-anchored: never bind a host-root package that owns the bare name
    from _selfimport import anchor as _anchor
except ImportError:  # pragma: no cover - _HERE is always on sys.path via __init__
    _anchor = None


def _late(name):
    """Sibling module for LATE (post-register, host-origin) calls. The F1
    purge deletes the bare '_selfimport' key, so fall back to OUR package
    __init__'s resolver (package-qualified; F-2 fix route b). The
    module-namespace loader route (no package, no bare key) keeps the
    legacy importlib last-resort."""
    try:
        from _selfimport import anchor
        return anchor(name)
    except Exception:
        try:
            import __init__ as _pkg  # only ever OUR package __init__
            return _pkg._sibling(name)
        except Exception:
            import importlib
            return importlib.import_module(name)


def _sib(name):
    if _anchor is not None:
        return _anchor(name)
    return _late(name)

import pgconn  # noqa: E402  (resolved last-resort; engine funcs use _sib at call time)

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
    """Context engine bound to one profile: schema lcm2_<agent> on lcm2db.

    Duck-typed against agent.context_engine.ContextEngine: it satisfies the
    host protocol (name, token-state fields, update_from_response,
    should_compress, compress, get_tool_schemas, handle_tool_call,
    on_session_start, get_status, clone_for_agent, update_model) WITHOUT
    importing it: the plugin never imports host modules (R3 isolation). The
    host's isinstance() gate in ctx.register_context_engine therefore rejects
    a shared global registration; the sanctioned host-native path is
    context.engine: lcm2-pg -> plugins/context_engine/lcm2-pg/ (shim that
    subclasses the base and reuses these methods), proven by the R12 suite.
    """

    name = "lcm2-pg"  # host-facing engine id (context.engine value)

    # host token-state fields (run_agent.py reads these directly)
    last_prompt_tokens: int = 0
    last_completion_tokens: int = 0
    last_total_tokens: int = 0
    threshold_tokens: int = 0
    context_length: int = 0
    compression_count: int = 0
    threshold_percent: float = DEFAULT_THRESHOLD

    # Compaction-shape fields the host's preflight reads off
    # agent.context_compressor as BARE attributes (turn_context_compaction.py:
    # protect_first_n/protect_last_n/threshold_tokens/summary_target_ratio/
    # last_real_prompt_tokens). agent.context_engine.ContextEngine cannot be
    # inherited without a host import (R3), so we mirror the base-class
    # defaults verbatim (protect_first_n=3, protect_last_n=6) plus the fields
    # ContextCompressor carries that the same code paths read.
    protect_first_n: int = 3
    protect_last_n: int = 6
    emit_automatic_compaction_status: bool = True
    summary_target_ratio: float = 0.2  # ContextCompressor default (idle floor)

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
                v = r[0]
                covered.update(v if isinstance(v, list) else json.loads(v))
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
        return _late("tools").lcm_grep(args, engine=self)

    def tools_lcm_expand(self, args):
        return _late("tools").lcm_expand(args, engine=self)

    # -- host ContextEngine protocol (duck-typed; no host imports, R3) --
    # Bare attributes the host's compression paths read via getattr() on
    # agent.context_compressor (conversation_compression.py /
    # turn_context_compaction.py: last_real_prompt_tokens is read UNGUARDED
    # in a log line). Absent attributes were the F1 host-real crash
    # ('LCM2Engine' object has no attribute 'protect_first_n'); getattr-only
    # reads degrade to falsy defaults, so plain class-level defaults suffice.
    last_real_prompt_tokens: int = 0
    model_thresholds: dict = {}
    _config_context_length: int = 0
    _last_compress_refused_would_grow: bool = False
    _last_compress_aborted: bool = False
    _last_summary_error: str | None = None

    def update_from_response(self, usage: dict) -> None:
        self.last_prompt_tokens = int(usage.get("prompt_tokens") or 0)
        self.last_completion_tokens = int(usage.get("completion_tokens") or 0)
        self.last_total_tokens = int(usage.get("total_tokens") or 0)
        self.last_real_prompt_tokens = self.last_prompt_tokens

    def should_compress(self, prompt_tokens: int | None = None) -> bool:
        pt = prompt_tokens if prompt_tokens is not None \
            else self.last_prompt_tokens
        return bool(self.context_length
                    and pt >= self.threshold_tokens > 0)

    def compress(self, messages, current_tokens=None, focus_topic=None,
                 force=False, memory_context=""):
        """Persist every OpenAI-format message into the store (lossless) and
        run the DAG compression for this session. The transcript list is
        returned UNMODIFIED: nothing is removed from the live context —
        store-side compaction is the product (summaries stay retrievable via
        the lcm_* tools)."""
        sid = self._session_from_env()
        for m in messages or []:
            role = m.get("role") or "user"
            content = m.get("content")
            if not isinstance(content, str):
                content = json.dumps(content, ensure_ascii=False,
                                     default=str) if content else ""
            self.ingest(sid, role, content,
                        tool_calls=m.get("tool_calls"),
                        tool_name=m.get("name"),
                        tool_call_id=m.get("tool_call_id"))
        res = self.maybe_compress(sid)
        if res.get("compressed"):
            self.compression_count += 1
        return list(messages or [])

    def should_compress_preflight(self, messages) -> bool:
        return True

    def has_content_to_compress(self, messages) -> bool:
        return bool(messages)

    def prune_tool_results_only(self, messages, current_tokens=None):
        return messages, 0

    def on_turn_complete(self, messages, usage=None, **kwargs) -> None:
        try:
            self.maybe_compress(self._session_from_env())
        except Exception as exc:  # never break the host turn
            logger.warning("lcm2-pg on_turn_complete: %s", exc)

    def on_session_start(self, session_id: str, **kwargs) -> None:
        self._pinned_session = session_id

    def on_session_reset(self) -> None:
        self._pinned_session = ""
        self.last_prompt_tokens = 0
        self.last_completion_tokens = 0
        self.last_total_tokens = 0
        self.compression_count = 0

    def _session_from_env(self) -> str:
        return (getattr(self, "_pinned_session", "")
                or os.environ.get("HERMES_SESSION_ID") or "default")

    def get_tool_schemas(self) -> list:
        S = _sib("schemas")
        return [S.TOOLS[n] for n in sorted(S.TOOLS)]

    def handle_tool_call(self, name: str, args: dict, **kwargs) -> str:
        # NEVER import the bare name `tools` here: the host root and other
        # plugin dirs also own a top-level `tools` module, and under the
        # context-engine loader a bare import can resolve to a FOREIGN one
        # (every tool then answers "Unknown context engine tool"). Resolve
        # OUR OWN tools.py by file location, like pgconn._own_ddl().
        T = self._own_tools()
        fn = getattr(T, name, None)
        if fn is None or not callable(fn) or not name.startswith("lcm_"):
            return json.dumps({"error": f"Unknown context engine tool: {name}"})
        try:
            out = fn(args or {}, engine=self)
            return out if isinstance(out, str) else json.dumps(
                {"result": out}, default=str)
        except Exception as exc:  # well-formed JSON, never a crash
            return json.dumps({"error": f"{type(exc).__name__}: {exc}",
                               "implementation": "lcm2-pg/native"})

    _tools_mod = None

    def _own_tools(self):
        import importlib.util
        import pathlib
        import sys
        path = pathlib.Path(__file__).resolve().parent / "tools.py"
        mod = LCM2Engine.__dict__.get("_tools_mod")
        if mod is not None and getattr(mod, "__file__", None) and \
                pathlib.Path(mod.__file__).resolve() == path:
            return mod
        # tools.py bare-imports our siblings (pgconn, engine, ...). NEVER
        # touch the global sys.path (host boot imports concurrently; any
        # host `import tools.<sub>` while our dir is on sys.path binds our
        # flat file — F1 boot crash). Serve the siblings through the
        # _selfimport._SibFinder (exact own names, transient) instead.
        spec = importlib.util.spec_from_file_location(
            "lcm2_pg_plugin_tools", path)
        mod = importlib.util.module_from_spec(spec)
        sys.modules.setdefault("lcm2_pg_plugin_tools", mod)
        try:
            from _selfimport import _SibFinder as _SF  # ours, by file
        except Exception:
            import sys as _s
            _p = str(pathlib.Path(__file__).resolve().parent / "_selfimport.py")
            _sp = importlib.util.spec_from_file_location(
                "_lcm2_impl__selfimport", _p)
            _m = importlib.util.module_from_spec(_sp)
            _s.modules.setdefault("_lcm2_impl__selfimport", _m)
            _sp.loader.exec_module(_m)
            _SF = _m._SibFinder
        _finder = _SF()
        _inserted = _finder not in sys.meta_path
        if _inserted:
            sys.meta_path.insert(0, _finder)
        try:
            spec.loader.exec_module(mod)
        finally:
            if _inserted:
                try:
                    sys.meta_path.remove(_finder)
                except ValueError:
                    pass
        LCM2Engine._tools_mod = mod
        return mod

    def clone_for_agent(self):
        """Fresh engine sharing the durable backend (no deepcopy of pools)."""
        return LCM2Engine(dsn=self.dsn, schema=self.schema)

    def update_model(self, model: str, context_length: int, base_url: str = "",
                     api_key: str = "", provider: str = "",
                     api_mode: str = "") -> None:
        self.context_length = int(context_length or 0)
        self.threshold_tokens = int(self.context_length
                                    * self._threshold())
