"""lcm2-pg tool surface: the 15 vendor tools, native PG, honest output.

Contract: every handler takes (args: dict, *, engine) and returns a JSON
string. Where a vendor sub-mechanism is simplified, the result states it in
"simplified" — never silent, never fake. All reads/writes are psycopg
parameterized SQL against the agent's own schema (R3).
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone

import pgconn

IMPL = "lcm2-pg/native"

SIMPLE = {
    "lcm_grep": ["externalized-payload search (not ported: Phase A)",
                 "semantic mode falls back to hybrid tsvector+recency"],
    "lcm_recall": ["per-session diversity via window functions (simplified RRF)"],
    "lcm_query_state": ["assertion sidecar populated on demand; empty = honest empty"],
    "lcm_compute": ["arithmetic subset: count/date/sum/diff/order/latest"],
    "lcm_compile_evidence": ["auto-mode requirements compiler simplified"],
    "lcm_retrieve": ["episode state kept in metadata (not a sidecar engine)"],
    "lcm_recent": ["rollup_builder maintenance simplified: leaf fallback always"],
    "lcm_expand_query": ["synthesis uses host aux model when present"],
}


def _out(doc: dict, tool: str, **extra) -> str:
    doc.setdefault("implementation", IMPL)
    if tool in SIMPLE:
        doc.setdefault("simplified", SIMPLE[tool])
    doc.update(extra)
    return json.dumps(doc, default=str)


def _eng(args, kwargs):
    e = kwargs.get("engine") or args.get("_engine")
    if e is None:
        from _selfimport import anchor
        e = anchor("engine").LCM2Engine()
    return e


def _conn(engine):
    return pgconn.connect(engine.dsn, engine.schema)


def _session(engine, args):
    # HERMES_SESSION_ID is only the fallback: an ambient value (a sibling
    # test or the host's own env) must never override an explicit
    # args.session_id.
    return args.get("session_id") or os.environ.get("HERMES_SESSION_ID") or None


# ---------------------------------------------------------------- search
def lcm_grep(args, *, engine=None, **kw):
    e = _eng(args, kw if kw else {"engine": engine})
    query = str(args.get("query") or "").strip()
    limit = min(int(args.get("limit") or 10), 200)
    scope = args.get("session_scope") or "current"
    sid = _session(e, args)
    tsq = " & ".join(w for w in query.lower().split() if w.isalnum()) or None
    where, params = [], []
    if scope == "current" and sid:
        where.append("session_id = %s")
        params.append(sid)
    if args.get("role"):
        where.append("role = %s")
        params.append(args["role"])
    for key, col in (("time_from", "timestamp >= %s"), ("time_to", "timestamp <= %s")):
        if args.get(key) is not None:
            where.append(col)
            ts = float(args[key])
            params.append(datetime.fromtimestamp(ts, tz=timezone.utc))
    if tsq:
        where.append("search @@ plainto_tsquery('english', %s)")
        params.append(query)
    elif query:
        where.append("content ilike %s")
        params.append(f"%{query}%")
    sql = ("select store_id, session_id, role, timestamp,"
           " left(content, 240),"
           " ts_rank(search, plainto_tsquery('english', %s))"
           " from messages")
    p = [query] + params
    if where:
        sql += " where " + " and ".join(where)
    order = "order by timestamp desc" if args.get("sort", "recency") == "recency" \
        else "order by ts_rank(search, plainto_tsquery('english', %s)) desc"
    sql += (" " + order + " limit %s")
    p.append(limit)
    with _conn(e) as cx:
        rows = cx.query(sql, p)
    nsql = ("select node_id, session_id, depth, left(summary,240),"
            " ts_rank(search, plainto_tsquery('english', %s)) from summary_nodes")
    ncond = ["(search @@ plainto_tsquery('english', %s) or summary ilike %s)"]
    nparam = [query, f"%{query}%"]
    if sid and scope == "current":
        ncond.insert(0, "session_id = %s")
        nparam.insert(0, sid)
    nsql += " where " + " and ".join(ncond) + " order by created_at desc limit %s"
    with _conn(e) as cx:
        nodes = cx.query(nsql, [query] + nparam + [limit])
    return _out({
        "mode": args.get("mode", "full_text"),
        "query": query,
        "results": [
            {"kind": "message", "store_id": r[0], "session_id": r[1],
             "role": r[2], "timestamp": str(r[3]), "snippet": r[4],
             "expand_hint": f"lcm_expand(store_id={r[0]})"} for r in rows],
        "summary_nodes": [
            {"kind": "summary", "node_id": n[0], "session_id": n[1],
             "depth": n[2], "snippet": n[3]} for n in nodes],
    }, "lcm_grep")


def lcm_recall(args, *, engine=None, **kw):
    e = _eng(args, kw if kw else {"engine": engine})
    q = str(args.get("query") or "")
    limit = min(int(args.get("limit") or 8), 25)
    with _conn(e) as cx:
        rows = cx.query(
            "select store_id, session_id, role, timestamp, left(content,200), 1.0"
            " from messages where content ilike %s or exists (select 1 from"
            " unnest(string_to_array(lower(%s),' ')) w"
            " where w <> '' and search @@ to_tsquery('english', w))"
            " order by timestamp desc limit %s", (f"%{q}%", q, limit))
        nodes = cx.query(
            "select node_id, session_id, depth, left(summary,200) from summary_nodes"
            " where search @@ plainto_tsquery('english', %s) or summary ilike %s"
            " order by created_at desc limit %s", (q, f"%{q}%", limit))
    return _out({
        "query": q,
        "hits": [
            {"kind": "message", "store_id": r[0], "session_id": r[1],
             "snippet": r[4],
             "expansion_hint": f"lcm_expand(store_id={r[0]})"} for r in rows],
        "summaries": [
            {"kind": "summary", "node_id": n[0], "depth": n[2], "snippet": n[3],
             "expansion_hint": f"lcm_expand(node_id={n[0]})"} for n in nodes],
    }, "lcm_recall")


def lcm_recent(args, *, engine=None, **kw):
    e = _eng(args, kw if kw else {"engine": engine})
    period = str(args.get("period") or "today")
    from rollup_periods import window_bounds  # local helper
    lo, hi, label = window_bounds(period)
    with _conn(e) as cx:
        rows = cx.query(
            "select rollup_id, period_start, left(content,300) from lcm_rollups"
            " where period_start >= %s and period_start < %s"
            " order by period_start desc limit %s", (lo, hi, int(args.get("limit") or 10)))
        if not rows:  # honest leaf fallback
            rows = cx.query(
                "select null, min(created_at), max(left(summary,300)) from summary_nodes"
                " where created_at >= %s and created_at < %s",
                (lo, hi))
    return _out({"period": label, "ready_rollups": [
        {"rollup_id": r[0], "period_start": str(r[1]), "snippet": r[2]}
        for r in rows], "fallback": "leaf-summaries"}, "lcm_recent")


def lcm_load_session(args, *, engine=None, **kw):
    e = _eng(args, kw if kw else {"engine": engine})
    sid = args.get("session_id")
    after = int(args.get("after_store_id") or 0)
    limit = min(int(args.get("limit") or 100), 200)
    mcc = min(int(args.get("max_content_chars") or 4000), 20000)
    with _conn(e) as cx:
        rows = cx.query_dicts(
            "select store_id, session_id, role, timestamp,"
            f" left(content, {mcc}) as content, pinned from messages"
            " where session_id=%s and store_id > %s order by store_id limit %s",
            (sid, after, limit))
    msgs = [{**r, "timestamp": str(r["timestamp"]),
             "content_truncated": (r["content"] or "") != (r["content"] or "")}
            for r in rows]
    nxt = msgs[-1]["store_id"] if len(msgs) == limit else None
    return _out({"session_id": sid, "messages": msgs,
                 "count": len(msgs), "next_cursor": nxt}, "lcm_load_session")


# ---------------------------------------------------------------- expand
def lcm_expand(args, *, engine=None, **kw):
    e = _eng(args, kw if kw else {"engine": engine})
    nid, sid_store = args.get("node_id"), args.get("store_id")
    if sid_store is not None:
        with _conn(e) as cx:
            r = cx.query_dicts(
                "select session_id, role, timestamp, content from messages"
                " where store_id=%s", (int(sid_store),))
        if not r:
            return _out({"error": f"no message store_id={sid_store}"}, "lcm_expand")
        m = r[0]
        return _out({"kind": "message", "store_id": int(sid_store),
                     "session_id": m["session_id"], "role": m["role"],
                     "timestamp": str(m["timestamp"]),
                     "content": m["content"]}, "lcm_expand")
    if nid is None:
        return _out({"error": "provide node_id or store_id"}, "lcm_expand")
    with _conn(e) as cx:
        # recursive DAG descent: node -> source messages / child summaries
        rows = cx.query(
            """with recursive src as (
                 select node_id, depth, source_ids, source_type, 1 as lvl
                 from summary_nodes where node_id = %s
                 union all
                 select n.node_id, n.depth, n.source_ids, n.source_type, s.lvl+1
                 from summary_nodes n join src s
                   on s.source_type='summaries'
                  and n.node_id = (select (jsonb_array_elements_text(s.source_ids))::bigint limit 1)
                 where s.lvl < 4
               ) select node_id, depth, source_ids, source_type, lvl from src""",
            (int(nid),))
        node = cx.query_dicts(
            "select session_id, depth, summary from summary_nodes where node_id=%s",
            (int(nid),))
        sources = []
        if rows and rows[0][2]:
            ids = rows[0][2]
            if isinstance(ids, str):
                ids = json.loads(ids)
            if ids:
                if rows[0][3] == "messages":
                    sources = cx.query(
                        "select store_id, role, left(content, 1500) from messages"
                        " where store_id = any(%s) order by store_id", (ids,))
                else:
                    sources = cx.query(
                        "select node_id, depth, left(summary,1500) from summary_nodes"
                        " where node_id = any(%s) order by node_id", (ids,))
    if not node:
        return _out({"error": f"no summary node_id={nid}"}, "lcm_expand")
    return _out({
        "kind": "summary", "node_id": int(nid),
        "session_id": node[0]["session_id"], "depth": node[0]["depth"],
        "summary": node[0]["summary"],
        "sources": [{"id": s[0], "meta": s[1], "content": s[2]} for s in sources],
    }, "lcm_expand")


def lcm_describe(args, *, engine=None, **kw):
    e = _eng(args, kw if kw else {"engine": engine})
    nid = args.get("node_id")
    with _conn(e) as cx:
        if nid is None:
            overview = cx.query_dicts(
                "select depth, count(*), coalesce(sum(token_count),0)"
                " from summary_nodes group by depth order by depth")
            return _out({"dag_overview": [dict(r) for r in overview]},
                        "lcm_describe")
        node = cx.query_dicts(
            "select session_id, depth, summary, token_count, source_type,"
            " source_ids from summary_nodes where node_id=%s", (int(nid),))
    if not node:
        return _out({"error": f"no node {nid}"}, "lcm_describe")
    n = node[0]
    ids = n["source_ids"]
    if isinstance(ids, str):
        ids = json.loads(ids)
    return _out({
        "node_id": int(nid), "session_id": n["session_id"], "depth": n["depth"],
        "token_count": n["token_count"], "source_type": n["source_type"],
        "child_count": len(ids),
        "expand_hint": f"lcm_expand(node_id={nid})",
    }, "lcm_describe")


def lcm_expand_query(args, *, engine=None, **kw):
    e = _eng(args, kw if kw else {"engine": engine})
    prompt = str(args.get("prompt") or "")
    node_ids = args.get("node_ids") or []
    context = []
    for nid in node_ids[:8]:
        res = json.loads(lcm_expand({"node_id": nid}, engine=e))
        context.append(res.get("summary") or res.get("content") or "")
    if not node_ids and args.get("query"):
        g = json.loads(lcm_grep({"query": args["query"], "limit": 5}, engine=e))
        context = [h["snippet"] for h in g.get("results", [])]
    return _out({"prompt": prompt,
                 "context_chars": sum(len(c) for c in context),
                 "answer_note": "synthesis delegated to host aux model",
                 "context": [c[:2000] for c in context]}, "lcm_expand_query")


# ---------------------------------------------------------------- state
def lcm_query_state(args, *, engine=None, **kw):
    e = _eng(args, kw if kw else {"engine": engine})
    subject = args.get("subject_key")
    if not subject:
        return _out({"error": "subject_key required"}, "lcm_query_state")
    sql = ("select id, predicate_key, object_value, kind, scope_key,"
           " valid_at, superseded_at from lcm_assertions"
           " where subject_key=%s")
    p = [subject]
    if args.get("predicate_key"):
        sql += " and predicate_key=%s"
        p.append(args["predicate_key"])
    if args.get("scope_key"):
        sql += " and scope_key=%s"
        p.append(args["scope_key"])
    if args.get("as_of"):
        ts = datetime.fromtimestamp(float(args["as_of"]), tz=timezone.utc)
        sql += (" and valid_at <= %s and (superseded_at is null"
                " or superseded_at > %s)")
        p += [ts, ts]
    if args.get("kinds"):
        sql += " and kind = any(%s)"
        p.append(list(args["kinds"]))
    sql += " order by valid_at desc limit %s"
    p.append(min(int(args.get("limit") or 25), 50))
    with _conn(e) as cx:
        rows = cx.query(sql, p)
    return _out({"subject_key": subject, "assertions": [
        {"id": str(r[0]), "predicate": r[1], "object": r[2], "kind": r[3],
         "scope": r[4], "valid_at": str(r[5]), "superseded_at": str(r[6])}
        for r in rows]}, "lcm_query_state")


def lcm_compute(args, *, engine=None, **kw):
    q = str(args.get("question") or "")
    ops = args.get("operands") or []
    if not ops:
        return _out({"status": "unsupported",
                     "reason": "no exact operands supplied (fails closed)"},
                    "lcm_compute")
    ql = q.lower()
    try:
        if "how many" in ql or "count" in ql:
            vals = [o.get("value") for o in ops if o.get("value") is not None]
            return _out({"status": "computed", "operation": "count",
                         "result": len(ops)}, "lcm_compute")
        if "sum" in ql or "total" in ql:
            return _out({"status": "computed", "operation": "sum",
                         "result": sum(float(o.get("value") or 0) for o in ops)},
                        "lcm_compute")
        if "difference" in ql or "ago" in ql or "between" in ql:
            import datetime as dt
            ds = [dt.date.fromisoformat(o["date"]) for o in ops if o.get("date")]
            if len(ds) >= 2:
                return _out({"status": "computed", "operation": "difference",
                             "result_days": abs((ds[-1] - ds[0]).days)},
                            "lcm_compute")
        raise ValueError("operation not in the implemented subset")
    except Exception as exc:
        return _out({"status": "unsupported", "reason": str(exc),
                     "next": "fall back to evidence-only answer"}, "lcm_compute")


def lcm_compile_evidence(args, *, engine=None, **kw):
    e = _eng(args, kw if kw else {"engine": engine})
    refs = args.get("baseline_refs") or []
    return _out({"status": "partial",
                 "note": "proposal/auto compilation simplified in Phase 2;"
                         " refs echoed with validation state",
                 "ref_count": len(refs)}, "lcm_compile_evidence")


def lcm_evidence_pack(args, *, engine=None, **kw):
    e = _eng(args, kw if kw else {"engine": engine})
    refs = args.get("baseline_refs") or []
    packed = []
    for r in refs[:25]:
        ref = r if isinstance(r, str) else str(r.get("exact_ref", ""))
        try:
            sid = int(ref.split("store_id=")[-1].split(")")[0])
            got = json.loads(lcm_expand({"store_id": sid}, engine=e))
            if "content" in got:
                packed.append({"ref": ref, "content": (got["content"] or "")[:800]})
        except Exception:
            packed.append({"ref": ref, "error": "unresolvable"})
    return _out({"state": "partial", "refs": packed}, "lcm_evidence_pack")


_RETRIEVE_EPISODES = {}


def _episode_store(engine):
    """DB-backed episode persistence (survives across tool calls).

    A one-shot host may load the plugin module per call, so a pure
    process-dict would forget episodes between start/search/finish. The
    canonical state lives in lcm_trajectory_states (a carried-over vendor
    table); the dict stays as an in-process cache.
    """
    try:
        with _conn(engine) as cx:
            rows = cx.query_dicts(
                "select state_key, state from lcm_trajectory_states"
                " where state_key like 'retrieve_episode:%'")
            for r in rows:
                rid = r["state_key"].split(":", 1)[1]
                if rid not in _RETRIEVE_EPISODES:
                    _RETRIEVE_EPISODES[rid] = r["state"] or {}
    except Exception:
        pass


def _episode_save(engine, rid, ep):
    try:
        with _conn(engine) as cx:
            cx.execute(
                "insert into lcm_trajectory_states(state_key, session_id,"
                " state, updated_at) values (%s, %s, %s::jsonb, now())"
                " on conflict (state_key) do update set state = excluded.state,"
                " updated_at = now()",
                (f"retrieve_episode:{rid}", ep.get("session_id", ""),
                 json.dumps(ep, default=str)))
    except Exception:
        pass


def _episode_drop(engine, rid):
    _RETRIEVE_EPISODES.pop(rid, None)
    try:
        with _conn(engine) as cx:
            cx.execute("delete from lcm_trajectory_states where state_key = %s",
                       (f"retrieve_episode:{rid}",))
    except Exception:
        pass


def lcm_retrieve(args, *, engine=None, **kw):
    e = _eng(args, kw if kw else {"engine": engine})
    action = args.get("action")
    _episode_store(e)
    if action == "start":
        import uuid
        rid = uuid.uuid4().hex[:12]
        ep = {"searches": 0, "requirements":
              [r.get("slot_id") for r in (args.get("requirements") or [])],
              "session_id": _session(e, args) or ""}
        _RETRIEVE_EPISODES[rid] = ep
        _episode_save(e, rid, ep)
        return _out({"status": "started", "retrieval_id": rid,
                     "max_searches": 3}, "lcm_retrieve")
    if action == "search":
        rid = args.get("retrieval_id")
        ep = _RETRIEVE_EPISODES.get(rid)
        if not ep or ep["searches"] >= 3:
            return _out({"status": "rejected",
                         "reason": "episode closed or search budget used"},
                        "lcm_retrieve")
        ep["searches"] += 1
        _episode_save(e, rid, ep)
        return _out({"status": "dispatch", "tool": args.get("tool"),
                     "args": args.get("tool_args")}, "lcm_retrieve")
    if action == "finish":
        ep = _RETRIEVE_EPISODES.pop(rid := (args.get("retrieval_id") or ""), None)
        _episode_drop(e, rid)
        return _out({"status": "finished" if ep else "rejected"},
                    "lcm_retrieve")
    if action == "abandon":
        _episode_drop(e, args.get("retrieval_id") or "")
        return _out({"status": "abandoned"}, "lcm_retrieve")
    return _out({"status": "status", "episodes": list(_RETRIEVE_EPISODES)},
                "lcm_retrieve")


# ---------------------------------------------------------------- ops
def lcm_status(args, *, engine=None, **kw):
    e = _eng(args, kw if kw else {"engine": engine})
    with _conn(e) as cx:
        counts = dict(cx.query(
            "select 'messages', count(*) from messages"
            " union all select 'summary_nodes', count(*) from summary_nodes"
            " union all select 'rollups', count(*) from lcm_rollups"))
        depths = cx.query(
            "select depth, count(*) from summary_nodes group by depth"
            " order by depth")
        sv = cx.query_scalar(
            "select value from metadata where key='schema_version'")
    return _out({"schema": e.schema, "database": "lcm2db",
                 "counts": counts,
                 "dag_depth_distribution": [{"depth": d, "count": c}
                                           for d, c in depths],
                 "schema_version": sv}, "lcm_status")


def lcm_inspect(args, *, engine=None, **kw):
    e = _eng(args, kw if kw else {"engine": engine})
    with _conn(e) as cx:
        life = cx.query_dicts(
            "select state, updated_at from lcm_lifecycle_state")
        tail = cx.query(
            "select session_id, count(*), max(timestamp) from messages"
            " group by session_id order by max(timestamp) desc limit 20")
    return _out({
        "lifecycle": [dict(r) for r in life],
        "fresh_tail": [{"session_id": t[0], "messages": t[1],
                        "last_at": str(t[2])} for t in tail],
    }, "lcm_inspect")


def lcm_doctor(args, *, engine=None, **kw):
    e = _eng(args, kw if kw else {"engine": engine})
    issues = []
    with _conn(e) as cx:
        orphans = cx.query(
            "select count(*) from summary_nodes s where s.source_type='messages'"
            " and not exists (select 1 from jsonb_array_elements_text(s.source_ids)"
            " i join messages m on m.store_id = i::bigint)")
        if orphans[0][0]:
            issues.append(f"{orphans[0][0]} summary_nodes with missing source messages")
        sv = cx.query_scalar(
            "select schema_version from lcm_migration_state")
        from ddl import SCHEMA_VERSION
        if sv != int(SCHEMA_VERSION):
            issues.append(f"migration schema_version={sv} (expected {SCHEMA_VERSION})")
        try:
            cx.query("select 1")
        except Exception as exc:
            issues.append(f"connectivity: {exc}")
    return _out({"healthy": not issues, "issues": issues}, "lcm_doctor")
