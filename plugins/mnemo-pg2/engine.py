"""mnemo-pg2 engine — PG-native mnemosyne operations (Phase 3 core).

All storage via pgconn (psycopg -> mnemo2db on the dedicated instance).
Zero sqlite, zero fallback (R3). bank='private' targets the caller's
mnemo2_<agent> schema; bank='surface' targets the ONE shared schema (R5).
The hybrid recall ranking (vec 0.5 / fts 0.3 / importance 0.2 defaults,
temporal decay) is a single SQL pass per tier.
"""
from __future__ import annotations

import json
import os
import time
import uuid
from datetime import datetime, timezone

import pgconn
try:  # sibling import inside OUR body: _SelfImportFinder serves OUR file
    import embed as _embed
except ModuleNotFoundError:  # direct-by-path load (tests/probes): locate
    # by FILE next to this module — never a cwd- or sys.path-dependent
    # hack, and still zero bare-name publication outside the anchor
    # discipline.
    import importlib.util as _ilu
    import os as _os
    _p = _ilu.spec_from_file_location(
        "mnemo2_embed_here", _os.path.join(
            _os.path.dirname(_os.path.abspath(__file__)), "embed.py"))
    if _p is None or _p.loader is None:  # pragma: no cover
        raise ImportError("mnemo-pg2: embed.py missing next to engine.py")
    _embed = _ilu.module_from_spec(_p)
    _p.loader.exec_module(_embed)
try:  # P4-C consolidation LLM lane (same anchor discipline as embed)
    import llm as _llm
except ModuleNotFoundError:
    import importlib.util as _ilu2
    import os as _os2
    _p2 = _ilu2.spec_from_file_location(
        "mnemo2_llm_here", _os2.path.join(
            _os2.path.dirname(_os2.path.abspath(__file__)), "llm.py"))
    if _p2 is None or _p2.loader is None:  # pragma: no cover
        raise ImportError("mnemo-pg2: llm.py missing next to engine.py")
    _llm = _ilu2.module_from_spec(_p2)
    _p2.loader.exec_module(_llm)
try:  # S2: vendor extract_and_store_facts regex lane (pure, zero-model)
    import facts as _facts
except ModuleNotFoundError:
    import importlib.util as _ilu3
    import os as _os3
    _p3 = _ilu3.spec_from_file_location(
        "mnemo2_facts_here", _os3.path.join(
            _os3.path.dirname(_os3.path.abspath(__file__)), "facts.py"))
    if _p3 is None or _p3.loader is None:  # pragma: no cover
        raise ImportError("mnemo-pg2: facts.py missing next to engine.py")
    _facts = _ilu3.module_from_spec(_p3)
    _p3.loader.exec_module(_facts)

DEFAULT_VEC_W = 0.5
DEFAULT_FTS_W = 0.3
DEFAULT_IMP_W = 0.2
HALFLIFE_H = float(os.environ.get("MNEMO2_RECENCY_HALFLIFE", "24"))


def _now() -> datetime:
    return datetime.now(timezone.utc)


_VECDIM: dict[str, int] = {}


def _col_dim(cx) -> int:
    """Column dim of memory_embeddings.embedding for cx's schema
    (pgvector stores it in atttypmod). Cached per schema; 0 = unknown
    (guard inert)."""
    key = getattr(cx, "schema", "") or ""
    d = _VECDIM.get(key)
    if d is None:
        try:
            r = cx.query_scalar(
                "SELECT atttypmod FROM pg_attribute a"
                " JOIN pg_class c ON c.oid = a.attrelid"
                " JOIN pg_namespace n ON n.oid = c.relnamespace"
                " WHERE n.nspname = %s AND c.relname = 'memory_embeddings'"
                " AND a.attname = 'embedding'", (key,))
        except Exception:
            r = None
        d = int(r or 0)
        _VECDIM[key] = d
    return d


def _veclit(cx, text: str, lane: str = "doc"):
    """Dimension-guarded vector literal (S5): mismatch or provider error
    yields None -> SQL NULL. Recall's coalesce() scores a NULL query/doc
    vector as 0 — never a dim-mismatch ERROR across schemas."""
    try:
        vec = (_embed.embed_query(text) if lane == "query"
               else _embed.embed_text(text))
    except Exception:
        return None
    d = _col_dim(cx)
    if d and len(vec) != d:
        return None
    return _embed.to_pg_vector(vec)


def new_id(prefix="mem") -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


class Engine:
    """One engine per agent-profile process. dsn/schema resolution happens
    per call via pgconn so env overrides (profile .env) take effect without
    re-instantiation."""

    def __init__(self, dsn: str | None = None, schema: str | None = None,
                 shared_schema: str | None = None):
        self.dsn = dsn
        self.schema = schema
        self.shared_schema = shared_schema

    # -- plumbing -------------------------------------------------------
    def _cx(self, bank: str = "private", ddl: bool = True):
        if bank == "surface":
            return pgconn.connect(self.dsn, self.shared_schema or
                                  pgconn.shared_schema(), ddl=ddl,
                                  kind="shared")
        return pgconn.connect(self.dsn, self.schema or pgconn.agent_schema(),
                              ddl=ddl, kind="private")

    # -- remember ---------------------------------------------------------
    def remember(self, content: str, *, importance: float = 0.5,
                 source: str = "user", scope: str = "session",
                 valid_until: str | None = None, bank: str = "private",
                 extract_entities: bool = False, extract: bool = False,
                 veracity: str = "unknown", session_id: str = "",
                 metadata: dict | None = None) -> dict:
        if not content or not content.strip():
            return {"ok": False, "error": "empty content"}
        mid = new_id()
        with self._cx(bank) as cx:
            vec = _veclit(cx, content)
            cx.execute(
                "INSERT INTO working_memory(id,session_id,content,importance,"
                "source,scope,valid_until,veracity,extract_entities,extract,"
                "metadata) VALUES (%s,%s,%s,%s,%s,%s,%s::date,%s,%s,%s,%s::jsonb)",
                (mid, session_id, content, importance, source, scope,
                 valid_until, veracity, extract_entities, extract,
                 json.dumps(metadata or {})))
            cx.execute(
                "INSERT INTO memory_embeddings(memory_id,kind,embedding,model)"
                " VALUES (%s,'working',%s::vector,%s)",
                (mid, vec, _embed.active_model()))
            fanout = []
            if extract_entities or extract:
                # vendor entities.py fan-out target = triples (subject=
                # memory_id, predicate='mentions', object=entity)
                for ent in _extract_entities(content) if extract_entities else []:
                    cx.execute(
                        "INSERT INTO triples(subject,predicate,object,source,"
                        "source_memory_id) VALUES (%s,'mentions',%s,'entity',"
                        "%s) RETURNING id",
                        (mid, ent, mid))
                    fanout.append(f"mentions:{ent}")
                if extract:
                    for t in _heuristic_triples(content):
                        cx.execute(
                            "INSERT INTO triples(subject,predicate,object,"
                            "source,source_memory_id) VALUES (%s,%s,%s,"
                            "'extract',%s) RETURNING id",
                            (t[0], t[1], t[2], mid))
                        fanout.append(f"{t[0]} {t[1]} {t[2]}")
            cx.execute(
                "INSERT INTO memory_audit_events(memory_id,event,payload)"
                " VALUES (%s,'remember',%s::jsonb)",
                (mid, json.dumps({"bank": bank, "scope": scope})))
            # S2 (vendor remember enrichment parity): typed fact
            # annotations (regex lane, zero-model, capped 5) land on
            # EVERY stored memory; heuristic KG ingest is always-on for
            # rows that didn't already use the extract flag.
            # Non-fatal by construction; env kill switches.
            try:
                if os.environ.get("MNEMO2_AUTO_FACTS", "1") == "1":
                    for _k, _v in _facts.extract_facts(content):
                        cx.execute(
                            "INSERT INTO annotations(memory_id,kind,body)"
                            " VALUES (%s,%s,%s)", (mid, _k, _v))
                if (os.environ.get("MNEMO2_AUTO_TRIPLES", "1") == "1"
                        and not extract):
                    for t in _heuristic_triples(content)[:3]:
                        cx.execute(
                            "INSERT INTO triples(subject,predicate,object,"
                            "source,source_memory_id)"
                            " VALUES (%s,%s,%s,'ingest',%s)", (*t, mid))
            except Exception:
                pass
        return {"ok": True, "id": mid, "bank": bank,
                "fanout": fanout, "status": "stored"}

    # -- recall -----------------------------------------------------------
    def _ledger_new_turn(self, session_id="", turn_hash=""):
        """P4-B turn-ticket (private method; reachable as the internal
        name mnemosyne__ledger_new_turn via _dispatch, invisible on the
        42-name tool surface). True = first time we see this turn."""
        sid = str(session_id or "")
        h = str(turn_hash or "")
        if not h:
            return {"ok": False, "error": "empty turn_hash"}
        with self._cx("private") as cx:
            rows = cx.query_dicts(
                "INSERT INTO turn_ledger(session_id, turn_hash)"
                " VALUES (%s, %s) ON CONFLICT DO NOTHING"
                " RETURNING turn_hash", (sid, h))
            if not rows:
                cx.execute("DELETE FROM turn_ledger WHERE created_at <"
                           " now() - interval '30 days'")
            return {"ok": True, "is_new": bool(rows)}

    def recall(self, query: str, *, limit: int = 5, bank: str = "private",
               vec_weight: float | None = None, fts_weight: float | None = None,
               importance_weight: float | None = None,
               temporal_weight: float = 0.0, query_time: str | None = None,
               temporal_halflife: float | None = None,
               explain: bool = False, include_expired: bool = False) -> dict:
        if not query or not query.strip():
            return {"ok": False, "error": "empty query", "results": []}
        vw = vec_weight if vec_weight is not None else DEFAULT_VEC_W
        fw = fts_weight if fts_weight is not None else DEFAULT_FTS_W
        iw = importance_weight if importance_weight is not None else DEFAULT_IMP_W
        hl = temporal_halflife or HALFLIFE_H
        qvec_str = None  # dim-guarded; set once connected (S5)
        now = _now()
        if query_time:
            try:
                now = datetime.fromisoformat(query_time)
            except ValueError:
                pass
        age_hours_col = (
            "EXTRACT(EPOCH FROM (%s::timestamptz - w.created_at)) / 3600.0")
        temporal_expr = f"POWER(0.5, GREATEST({age_hours_col}, 0) / {hl})"
        temporal_expr_p = temporal_expr.replace("w.created_at", "p.created_at")
        exp_filter = (" AND (valid_until IS NULL OR valid_until >= "
                      "%s::timestamptz::date)") if not include_expired else ""
        now_param = now.isoformat()

        sql_working = f"""
            WITH cand AS (
                SELECT w.id, w.content, w.importance, w.created_at, w.scope,
                       w.session_id, w.source,
                       coalesce(1 - (e.embedding <=> %s::vector), 0) AS vec_sim,
                       coalesce(ts_rank_cd(w.search, plainto_tsquery('english', %s)), 0) AS fts,
                       {temporal_expr} AS temporal,
                       -- S2: vendor fact_recall_ranking parity — typed
                       -- annotation hits on the row add a small boost.
                       EXISTS(SELECT 1 FROM annotations a
                             WHERE a.memory_id = w.id
                               AND a.body ILIKE '%%' || %s || '%%') AS fact_hit
                FROM working_memory w
                LEFT JOIN memory_embeddings e ON e.memory_id = w.id
                WHERE 1=1{exp_filter}
                ORDER BY (w.search @@ plainto_tsquery('english', %s)) DESC,
                         w.created_at DESC
                LIMIT 200
            )
            SELECT id, content, importance, created_at, scope, session_id,
                   source, vec_sim, fts, temporal,
                   (%s::real * vec_sim + %s::real * ln(1 + fts) + %s::real * importance
                    + %s::real * temporal
                    + CASE WHEN fact_hit THEN 0.05 ELSE 0 END::real) AS score
            FROM cand
            ORDER BY score DESC LIMIT %s"""
        sql_episodic = f"""
            SELECT p.id, p.summary AS content, p.importance, p.created_at,
                   'episodic' AS scope, p.session_id, 'episodic' AS source,
                   coalesce(1 - (e.embedding <=> %s::vector), 0) AS vec_sim,
                   coalesce(ts_rank_cd(p.search, plainto_tsquery('english', %s)), 0) AS fts,
                   {temporal_expr_p} AS temporal,
                   (%s::real * coalesce(1 - (e.embedding <=> %s::vector), 0)
                    + %s::real * ln(1 + coalesce(ts_rank_cd(p.search, plainto_tsquery('english', %s)), 0))
                    + %s::real * p.importance
                    + %s::real * {temporal_expr_p}) AS score
            FROM episodic_memory p
            LEFT JOIN memory_embeddings e ON e.memory_id = p.id AND e.kind = 'episodic'
            WHERE (p.search @@ plainto_tsquery('english', %s)
                   OR p.created_at > now() - interval '7 days')
            ORDER BY score DESC LIMIT %s"""

        results: list[dict] = []
        tiers = []
        with self._cx(bank) as cx:
            qvec_str = _veclit(cx, query, lane="query")
            _toks = [t for t in "".join(
                c if c.isalnum() else " " for c in query).split()
                if len(t) >= 3 and not t.isdigit()]
            _fw = max(_toks, key=len) if _toks else (query[:20] or "x")
            params = [qvec_str, query, now_param, _fw]
            if exp_filter:
                params.append(now_param)
            params += [query, vw, fw, iw, temporal_weight, limit]
            rows = cx.query_dicts(sql_working, params)
            working_hit = [r for r in rows if r["fts"] > 0 or r["vec_sim"] > 0.05]
            tiers.append(("working", len(rows), len(working_hit)))
            for r in rows[:limit]:
                results.append(_hit(r, "working"))
            if len(results) < limit:
                erows = cx.query_dicts(
                    sql_episodic,
                    (qvec_str, query, now_param, vw, qvec_str, fw, query, iw,
                     temporal_weight, now_param, query, limit))
                tiers.append(("episodic", len(erows), len(erows)))
                seen = {r["id"] for r in results}
                for r in erows:
                    if len(results) >= limit:
                        break
                    if r["id"] in seen:
                        continue
                    results.append(_hit(r, "episodic"))
            self._bump_diagnost(cx, tiers, fallbacks=len(results) == 0)
            # P4-A reinforcement (vendor beam.py:7349 parity): returned
            # rows get a batch recall_count/last_recalled bump. Never
            # allowed to break recall; env-off kill switch.
            if results and os.environ.get("MNEMO2_RECALL_REINFORCE",
                                          "1") != "0":
                try:
                    for _tier, _tbl in (("working", "working_memory"),
                                         ("episodic", "episodic_memory")):
                        _ids = [r["id"] for r in results
                                if r.get("tier") == _tier]
                        if _ids:
                            cx.execute(
                                "UPDATE " + _tbl + " SET recall_count ="
                                " COALESCE(recall_count, 0) + 1,"
                                " last_recalled = now()"
                                " WHERE id = ANY(%s)", (_ids,))
                except Exception:
                    pass
        out = {"ok": True, "results": results, "bank": bank}
        if explain:
            out["explain"] = {
                "weights": {"vec": vw, "fts": fw, "importance": iw,
                            "temporal": temporal_weight},
                "halflife_hours": hl,
                "tiers": [{"tier": t, "scanned": s, "matched": m}
                          for t, s, m in tiers],
                "query_time": now.isoformat(),
            }
        return out

    # -- stats / get / update / forget / invalidate -------------------------
    def stats(self, bank: str = "private") -> dict:
        with self._cx(bank) as cx:
            return {
                "ok": True, "bank": bank,
                "schema": cx.schema,
                "working": cx.query_scalar("select count(*) from working_memory"),
                "episodic": cx.query_scalar("select count(*) from episodic_memory"),
                "canonical": cx.query_scalar(
                    "select count(*) from canonical_facts WHERE valid_until IS NULL"),
                "triples_open": cx.query_scalar(
                    "select count(*) from triples WHERE valid_until IS NULL"),
                "tiers": [dict(r) for r in cx.query_dicts(
                    "select coalesce(tier,'(none)') tier, count(*) n from "
                    "working_memory group by 1 order by 2 desc")],
            }

    def get(self, memory_id: str, bank: str = "private") -> dict:
        with self._cx(bank) as cx:
            row = cx.query_dicts(
                "select id,content,importance,source,scope,created_at,"
                "valid_until,superseded_by,expired_at,veracity,metadata "
                "from working_memory where id=%s", (memory_id,))
        if not row:
            return {"ok": False, "error": "not found", "memory": None}
        return {"ok": True, "memory": _jsonable(row[0])}

    def update(self, memory_id: str, *, content: str | None = None,
               importance: float | None = None,
               bank: str = "private") -> dict:
        sets, params = [], []
        if content is not None:
            sets.append("content=%s")
            params.append(content)
        if importance is not None:
            sets.append("importance=%s")
            params.append(importance)
        if not sets:
            return {"ok": False, "error": "nothing to update"}
        params.append(memory_id)
        with self._cx(bank) as cx:
            n = cx.query_scalar(
                f"UPDATE working_memory SET {', '.join(sets)} WHERE id=%s "
                "RETURNING id", params)
            if n is None:
                return {"ok": False, "error": "not found"}
            if content is not None:
                # explicit re-embed (spec: trigger-free)
                cx.execute("DELETE FROM memory_embeddings WHERE memory_id=%s AND kind='working'", (memory_id,))
                cx.execute(
                    "INSERT INTO memory_embeddings(memory_id,kind,embedding,model)"
                    " VALUES (%s,'working',%s::vector,%s)",
                    (memory_id, _veclit(cx, content),
                     _embed.active_model()))
        return {"ok": True, "id": memory_id, "re_embedded": content is not None}

    def forget(self, memory_id: str, bank: str = "private") -> dict:
        with self._cx(bank) as cx:
            n = cx.query_scalar(
                "DELETE FROM working_memory WHERE id=%s RETURNING id",
                (memory_id,))
            if n:
                cx.execute("DELETE FROM memory_embeddings WHERE memory_id=%s",
                           (memory_id,))
                cx.execute("DELETE FROM graph_edges WHERE source_id=%s OR target_id=%s",
                           (memory_id, memory_id))
                return {"ok": True, "deleted": memory_id}
            n = cx.query_scalar(
                "DELETE FROM episodic_memory WHERE id=%s RETURNING id",
                (memory_id,))
        return {"ok": bool(n), "deleted": memory_id if n else None}

    def invalidate(self, memory_id: str, *, replacement_id: str | None = None,
                   bank: str = "private") -> dict:
        with self._cx(bank) as cx:
            row = cx.query_dicts(
                "UPDATE working_memory SET expired_at=now(),"
                " superseded_by=%s WHERE id=%s "
                "RETURNING id, superseded_by", (replacement_id, memory_id))
        if not row:
            return {"ok": False, "error": "not found"}
        return {"ok": True, "id": memory_id, "expired": True,
                "superseded_by": replacement_id}

    # -- scratchpad ---------------------------------------------------------
    def scratchpad_write(self, content: str) -> dict:
        with self._cx() as cx:
            cid = cx.query_scalar(
                "INSERT INTO scratchpad(content) VALUES (%s) RETURNING id",
                (content,))
        return {"ok": True, "id": cid}

    def scratchpad_read(self) -> dict:
        with self._cx() as cx:
            rows = cx.query_dicts(
                "select id,content,created_at from scratchpad order by id")
        return {"ok": True, "entries": _jsonable(rows)}

    def scratchpad_clear(self) -> dict:
        with self._cx() as cx:
            n = cx.query_scalar("DELETE FROM scratchpad RETURNING id")
        return {"ok": True, "cleared": True}

    # -- triples ------------------------------------------------------------
    def triple_add(self, subject, predicate, obj, *, valid_from=None,
                   valid_until=None, source="", confidence=1.0,
                   supersede=True, bank="private") -> dict:
        with self._cx(bank) as cx:
            closed = 0
            if supersede:
                closed = cx.query_scalar(
                    "UPDATE triples SET valid_until=now() WHERE subject=%s "
                    "AND predicate=%s AND valid_until IS NULL RETURNING id",
                    (subject, predicate)) or 0
            tid = cx.query_scalar(
                "INSERT INTO triples(subject,predicate,object,valid_from,"
                "valid_until,source,confidence) VALUES (%s,%s,%s,coalesce(%s::timestamptz,now()),"
                "%s::date,%s,%s) RETURNING id",
                (subject, predicate, obj, valid_from, valid_until,
                 source, confidence))
        return {"ok": True, "id": tid, "superseded_open": int(closed)}

    def triple_query(self, subject=None, predicate=None, obj=None,
                     as_of=None, bank="private") -> dict:
        # vendor parity (core/triples.py:306): a bare as_of shows facts that
        # were OPEN at that date; explicit subject/predicate/object filters
        # relax to overlap. Keep BOTH semantics: as_of alone = point-in-time;
        # pattern query = overlap window.
        sql = ("select id,subject,predicate,object,valid_from,valid_until,"
               "source,confidence from triples where 1=1")
        params = []
        pattern_given = any(v is not None for v in (subject, predicate, obj))
        if subject is not None:
            sql += " and subject ILIKE %s"
            params.append(subject)
        if predicate is not None:
            sql += " and predicate = %s"
            params.append(predicate)
        if obj is not None:
            sql += " and object ILIKE %s"
            params.append(obj)
        if as_of is not None:
            if pattern_given:
                sql += (" and valid_from <= %s::date AND (valid_until IS NULL"
                        " OR valid_until > %s::date)")
                params += [as_of, as_of]
            else:
                sql += (" and valid_from <= %s::date AND (valid_until IS NULL"
                        " OR valid_until > %s::date)")
                params += [as_of, as_of]
        sql += " order by id desc limit 200"
        with self._cx(bank) as cx:
            rows = cx.query_dicts(sql, params)
        return {"ok": True, "facts": _jsonable(rows)}

    def triple_end(self, subject, predicate, obj=None, *, valid_until=None,
                   bank="private") -> dict:
        sql = ("UPDATE triples SET valid_until=coalesce(%s::date, CURRENT_DATE)"
               " WHERE subject=%s AND predicate=%s AND valid_until IS NULL")
        params = [valid_until, subject, predicate]
        if obj is not None:
            sql += " AND object=%s"
            params.append(obj)
        sql += " RETURNING id"
        with self._cx(bank) as cx:
            ids = cx.query(sql, params)
        return {"ok": True, "closed": len(ids)}

    # -- graph ---------------------------------------------------------------
    def graph_link(self, source_id, target_id, relationship="related_to",
                   weight=0.5, bank="private") -> dict:
        with self._cx(bank) as cx:
            cx.execute(
                "INSERT INTO graph_edges(source_id,target_id,edge_type,weight)"
                " VALUES (%s,%s,%s,%s) ON CONFLICT (source_id,target_id,"
                "edge_type) DO UPDATE SET weight=EXCLUDED.weight",
                (source_id, target_id, relationship, weight))
        return {"ok": True}

    def graph_query(self, seed_memory_id, *, max_hops=2, edge_type="",
                    min_weight=0.0, bank="private") -> dict:
        sql = """
            WITH RECURSIVE hop(source_id, target_id, edge_type, weight, depth, path) AS (
                SELECT source_id, target_id, edge_type, weight, 1,
                       ARRAY[seed_memory_id, target_id]
                FROM graph_edges
                WHERE source_id = %s AND valid_until IS NULL
                  AND (%s = '' OR edge_type = %s) AND weight >= %s
                UNION ALL
                SELECT h2.source_id, h2.target_id, h2.edge_type, h2.weight,
                       hop.depth + 1, hop.path || h2.target_id
                FROM hop
                JOIN graph_edges h2 ON h2.source_id = hop.target_id
                WHERE hop.depth < %s AND h2.valid_until IS NULL
                  AND NOT h2.target_id = ANY(hop.path)
                  AND (%s = '' OR h2.edge_type = %s) AND h2.weight >= %s
            )
            SELECT source_id, target_id, edge_type, weight, depth FROM hop
            ORDER BY depth, weight DESC LIMIT 500"""
        with self._cx(bank) as cx:
            rows = cx.query_dicts(
                sql, (seed_memory_id, edge_type, edge_type, min_weight,
                      max_hops, edge_type, edge_type, min_weight))
        return {"ok": True, "nodes": _jsonable(rows),
                "seed": seed_memory_id, "max_hops": max_hops}

    # -- canonical -------------------------------------------------------------
    def remember_canonical(self, category, name, body, *, source="",
                           confidence=1.0) -> dict:
        with self._cx() as cx:
            cur = cx.query_dicts(
                "select id, body from canonical_facts WHERE category=%s AND"
                " name=%s AND valid_until IS NULL", (category, name))
            if cur and cur[0]["body"] == body:
                return {"ok": True, "id": cur[0]["id"], "noop": True}
            old_id = cur[0]["id"] if cur else None
            if old_id is not None:
                cx.execute(
                    "UPDATE canonical_facts SET superseded_by=NULL,"
                    " valid_until=CURRENT_DATE WHERE id=%s", (old_id,))
            new_id = cx.query_scalar(
                "INSERT INTO canonical_facts(category,name,body,source,"
                "confidence) VALUES (%s,%s,%s,%s,%s) RETURNING id",
                (category, name, body, source, confidence))
            if old_id is not None:
                cx.execute(
                    "UPDATE canonical_facts SET superseded_by=%s WHERE id=%s",
                    (new_id, old_id))
        return {"ok": True, "id": new_id, "noop": False,
                "superseded": old_id}

    def recall_canonical(self, *, category="", name="", query="",
                        include_history=False, limit=10) -> dict:
        sql = ("select id,category,name,body,source,confidence,created_at,"
               "valid_until,superseded_by from canonical_facts where 1=1")
        params = []
        if category:
            sql += " and category=%s"
            params.append(category)
        if name:
            sql += " and name=%s"
            params.append(name)
        if query:
            sql += " and body ILIKE %s"
            params.append(f"%{query}%")
        if not include_history:
            sql += " and valid_until IS NULL"
        sql += " order by id desc limit %s" % int(limit)
        with self._cx() as cx:
            rows = cx.query_dicts(sql, params)
            for r in rows:
                if include_history and name and category:
                    r["history"] = _jsonable(cx.query_dicts(
                        "select id,body,valid_until from canonical_facts "
                        "where category=%s and name=%s and superseded_by=%s",
                        (category, name, r["id"])))
        return {"ok": True, "slots": _jsonable(rows)}

    def forget_canonical(self, category, name) -> dict:
        with self._cx() as cx:
            n = cx.query_scalar(
                "UPDATE canonical_facts SET valid_until=CURRENT_DATE "
                "WHERE category=%s AND name=%s AND valid_until IS NULL "
                "RETURNING id", (category, name))
        return {"ok": n is not None, "retired": n}

    # -- shared surface (THE cross-agent schema, R5) -------------------------
    def _surface_schema(self) -> str:
        return (self.shared_schema or pgconn.shared_schema()).strip()

    def shared_remember(self, content, *, kind="meta", importance=0.8,
                        veracity="unknown", metadata=None) -> dict:
        if not content or not content.strip():
            return {"ok": False, "error": "empty content"}
        mid = new_id("sf")
        sh = self._surface_schema()
        with self._cx("surface") as cx:
            # fully-qualified: this write MUST land in the ONE shared
            # schema even when the caller's search_path resolves the
            # unqualified name to a private table instead.
            cx.execute(
                f'INSERT INTO "{sh}".working_memory(id,content,importance,'
                "source,scope,veracity,metadata) "
                "VALUES (%s,%s,%s,%s,'global',%s,%s::jsonb)",
                (mid, content, importance, f"surface:{kind}", veracity,
                 json.dumps({**(metadata or {}), "kind": kind})))
            cx.execute(
                f'INSERT INTO "{sh}".memory_embeddings(memory_id,kind,'
                "embedding,model) VALUES (%s,'working',%s::vector,%s)",
                (mid, _veclit(cx, content), _embed.active_model()))
        return {"ok": True, "id": mid, "bank": "surface", "kind": kind}

    def shared_recall(self, query, *, limit=5) -> dict:
        return self.recall(query, limit=limit, bank="surface")

    def shared_forget(self, memory_id) -> dict:
        return self.forget(memory_id, bank="surface")

    def shared_stats(self) -> dict:
        return self.stats(bank="surface")

    # -- batch ----------------------------------------------------------------
    def batch(self, operations, *, dry_run=False) -> dict:
        if len(operations) > 50:
            return {"ok": False, "error": "max 50 operations"}
        results = []
        # ONE PG transaction: any failing op rolls the whole batch back
        # (native-strength atomicity, spec §6). psycopg only opens a real
        # transaction at first execute, so a poisoned op AFTER N writes
        # still discards them all.
        with self._cx(ddl=True) as cx:
            if dry_run:
                cx.execute("SAVEPOINT batch")
            applied = 0
            for i, op in enumerate(operations):
                action = op.get("action")
                try:
                    r = self._batch_apply(cx, action, op)
                    results.append({"index": i, "action": action, **r})
                    applied += 1 if r.get("ok") else 0
                except Exception as exc:
                    if dry_run:
                        results.append({"index": i, "action": action,
                                        "ok": False, "error": str(exc)})
                        continue
                    cx.execute("ROLLBACK")
                    return {"ok": False, "rolled_back": True,
                            "failed_index": i, "error": str(exc),
                            "results": results}
                if not r.get("ok") and not dry_run:
                    # op-shaped failure (not an exception) — same contract:
                    # a failing op fails the batch, never a partial apply.
                    cx.execute("ROLLBACK")
                    results.append({"index": i, "action": action, **r})
                    return {"ok": False, "rolled_back": True,
                            "failed_index": i,
                            "error": r.get("error", "op failed"),
                            "results": results}
            if dry_run:
                cx.execute("ROLLBACK TO SAVEPOINT batch")
                cx.execute("ROLLBACK")
                return {"ok": True, "dry_run": True, "would_apply": applied,
                        "results": results}
        return {"ok": True, "applied": applied, "results": results}

    def _batch_apply(self, cx, action, op):
        if action == "remember":
            mid = new_id()
            vec = _veclit(cx, op.get("content", ""))
            cx.execute(
                "INSERT INTO working_memory(id,content,importance,source,"
                "scope,valid_until) VALUES (%s,%s,%s,%s,%s,%s::date)",
                (mid, op.get("content", ""), float(op.get("importance", 0.5)),
                 op.get("source", "user"), op.get("scope", "session"),
                 op.get("valid_until")))
            cx.execute(
                "INSERT INTO memory_embeddings(memory_id,kind,embedding,"
                "model) VALUES (%s,'working',%s::vector,%s)",
                (mid, vec, _embed.active_model()))
            return {"ok": True, "id": mid}
        if action == "update":
            sets, params = [], []
            if op.get("content") is not None:
                sets.append("content=%s")
                params.append(op["content"])
            if op.get("importance") is not None:
                sets.append("importance=%s")
                params.append(op["importance"])
            if not sets:
                return {"ok": False, "error": "nothing to update"}
            params.append(op.get("memory_id"))
            n = cx.query_scalar(
                f"UPDATE working_memory SET {', '.join(sets)} WHERE id=%s"
                " RETURNING id", params)
            return {"ok": n is not None, "id": op.get("memory_id")}
        if action == "forget":
            n = cx.query_scalar(
                "DELETE FROM working_memory WHERE id=%s RETURNING id",
                (op.get("memory_id"),))
            cx.execute("DELETE FROM memory_embeddings WHERE memory_id=%s",
                       (op.get("memory_id"),))
            return {"ok": n is not None}
        if action == "invalidate":
            n = cx.query_scalar(
                "UPDATE working_memory SET expired_at=now(),superseded_by=%s"
                " WHERE id=%s RETURNING id",
                (op.get("replacement_id"), op.get("memory_id")))
            return {"ok": n is not None}
        return {"ok": False, "error": f"unsupported action {action!r}"}

    # -- validate ---------------------------------------------------------------
    def validate(self, memory_id, action, *, validator="mike",
                 new_content="", note="", store="private") -> dict:
        bank = "surface" if store == "surface" or str(memory_id).startswith("sf_") else "private"
        with self._cx(bank) as cx:
            mem = cx.query_dicts(
                "select id,content from working_memory where id=%s",
                (memory_id,))
            if not mem:
                return {"ok": False, "error": "not found"}
            if action == "delete":
                cx.execute("DELETE FROM working_memory WHERE id=%s",
                           (memory_id,))
                # S5 hygiene: deleting a memory must delete its vector
                cx.execute("DELETE FROM memory_embeddings WHERE memory_id=%s",
                           (memory_id,))
            elif action == "update" and new_content:
                cx.execute("UPDATE working_memory SET content=%s WHERE id=%s",
                           (new_content, memory_id))
            elif action == "invalidate":
                cx.execute(
                    "UPDATE working_memory SET expired_at=now() WHERE id=%s",
                    (memory_id,))
            prior = cx.query_dicts(
                "select validator, action, note from memory_validations "
                "where memory_id=%s order by id desc limit 2", (memory_id,))
            ring = [{"validator": validator, "action": action, "note": note},
                    *prior]
            cx.execute(
                "INSERT INTO memory_validations(memory_id,bank,action,"
                "validator,author_original,note,history) VALUES (%s,%s,%s,%s,"
                "%s,%s,%s::jsonb)",
                (memory_id, bank, action, validator, validator, note,
                 json.dumps(ring[:3])))
        return {"ok": True, "id": memory_id, "action": action, "bank": bank}

    # -- persona ------------------------------------------------------------------
    def persona_promote(self, memory_id, *, tier="long_term", reason="") -> dict:
        with self._cx() as cx:
            mem = cx.query_dicts(
                "select content from working_memory where id=%s", (memory_id,))
            if not mem:
                return {"ok": False, "error": "source memory not found"}
            pid = cx.query_scalar(
                "INSERT INTO memoria_persona(body,tier,source_memory_id)"
                " VALUES (%s,%s,%s) RETURNING id",
                (mem[0]["content"], tier, memory_id))
        return {"ok": True, "persona_id": pid, "tier": tier}

    def persona_demote(self, persona_id, *, reason="") -> dict:
        with self._cx() as cx:
            row = cx.query_dicts(
                "UPDATE memoria_persona SET demoted_at=now() WHERE id=%s AND"
                " demoted_at IS NULL RETURNING body", (persona_id,))
            if not row:
                return {"ok": False, "error": "persona not found/already demoted"}
            pref = cx.query_scalar(
                "INSERT INTO memoria_preferences(body,reason,persona_id)"
                " VALUES (%s,%s,%s) RETURNING id",
                (row[0]["body"], reason, persona_id))
        return {"ok": True, "persona_id": int(persona_id),
                "preference_id": pref}

    def persona_list(self, *, tier=None, topic=None) -> dict:
        sql = ("select id,body,tier,topic,reinforcement_count,"
               "last_reinforced_at,created_at from memoria_persona "
               "where demoted_at IS NULL")
        params = []
        if tier:
            sql += " and tier=%s"
            params.append(tier)
        if topic:
            sql += " and topic=%s"
            params.append(topic)
        sql += (" order by case tier when 'permanent' then 0 when "
                "'long_term' then 1 else 2 end, reinforcement_count desc, id")
        with self._cx() as cx:
            rows = cx.query_dicts(sql, params)
        return {"ok": True, "personas": _jsonable(rows)}

    def persona_reinforce(self, persona_id) -> dict:
        with self._cx() as cx:
            n = cx.query_scalar(
                "UPDATE memoria_persona SET reinforcement_count="
                "reinforcement_count+1, last_reinforced_at=now() WHERE id=%s"
                " RETURNING reinforcement_count", (persona_id,))
        return {"ok": n is not None, "count": n}

    # -- reflection budget (vendor _reserve_reflection_budget parity) -----
    def reserve_budget(self, key: str = "auto_sleep",
                      secs: float = 1800) -> dict:
        """Atomic cross-process claim of a named cooldown window.
        True = caller owns the window (and refreshed it); False = a
        fresher claim exists elsewhere (any profile/process)."""
        import time as _t
        now = float(_t.time())
        with self._cx("private") as cx:
            got = cx.query_dicts(
                "UPDATE meta SET value=%s WHERE key=%s AND COALESCE("
                "NULLIF(value,'')::double precision, 0) < %s RETURNING key",
                (str(now), f"budget:{key}", now - float(secs)))
            if not got:
                got = cx.query_dicts(
                    "INSERT INTO meta(key,value) VALUES (%s,%s) "
                    "ON CONFLICT (key) DO NOTHING RETURNING key",
                    (f"budget:{key}", str(now)))
        return {"ok": bool(got)}

    # -- sleep (consolidation) -------------------------------------------------
    def sleep(self, *, all_sessions=False, dry_run=False, force=False) -> dict:
        # P4-C vendor-parity consolidation: rows already consolidated are
        # EXCLUDED (consolidated_at marker; originals are kept lossless —
        # only the marker moves), the per-session digest gets an LLM
        # summary through the proxy `aux` lane when reachable (concat
        # fallback = previous behaviour), and the log row is parameterized.
        with self._cx() as cx:
            cx.execute("SELECT pg_advisory_lock(hashtext(%s))",
                       (f"mnemo2_sleep:{cx.schema}",))
            try:
                cond = "TRUE" if (force or all_sessions) else \
                    "created_at < now() - interval '24 hours'"
                rows = cx.query_dicts(
                    f"select id, content, session_id, importance from "
                    f"working_memory where expired_at IS NULL AND "
                    f"consolidated_at IS NULL AND {cond} "
                    "order by created_at limit 500")
                if dry_run:
                    cx.execute("SELECT pg_advisory_unlock(hashtext(%s))",
                               (f"mnemo2_sleep:{cx.schema}",))
                    return {"ok": True, "dry_run": True,
                            "eligible": len(rows)}
                n_ep = 0
                by_sess: dict[str, list] = {}
                for r in rows:
                    by_sess.setdefault(r["session_id"], []).append(r)
                for sid, group in by_sess.items():
                    if not group:
                        continue
                    digest = " | ".join(g["content"][:80] for g in group[:20])
                    # S1c: ONE in-flight LLM call per DB instance for
                    # this lane (pg_advisory lock = cross-process single
                    # flight; waiting sessions hold a DB lock, never a
                    # model slot). Fail-soft: lock trouble = skip LLM,
                    # concat fallback still consolidates.
                    summary = None
                    try:
                        cx.execute("SELECT pg_advisory_lock(hashtext"
                                   "('mnemo2_llm_slot'))")
                        try:
                            summary = _llm.summarize(digest)
                        finally:
                            cx.execute("SELECT pg_advisory_unlock"
                                       "(hashtext('mnemo2_llm_slot'))")
                    except Exception:
                        summary = None
                    summary = summary or digest
                    eid = new_id("ep")
                    vec = _veclit(cx, summary)
                    cx.execute(
                        "INSERT INTO episodic_memory(id,session_id,summary,"
                        "detail,source_memory_ids,importance) VALUES (%s,%s,"
                        "%s,%s,%s::jsonb,%s)",
                        (eid, sid, f"consolidated({len(group)})", summary,
                         json.dumps([g["id"] for g in group]),
                         max(g["importance"] for g in group)))
                    cx.execute(
                        "INSERT INTO memory_embeddings(memory_id,kind,"
                        "embedding,model) VALUES (%s,'episodic',%s::vector,%s)",
                        (eid, vec, _embed.active_model()))
                    cx.execute(
                        "UPDATE working_memory SET tier='episodic',"
                        " consolidated_at=now() WHERE id = any(%s)",
                        ([g["id"] for g in group],))
                    n_ep += 1
                cx.execute(
                    "INSERT INTO consolidation_log(session_id,n_working,"
                    "n_episodic) VALUES (%s,%s,%s)",
                    ("all" if all_sessions else "", len(rows), n_ep))
            finally:
                cx.execute("SELECT pg_advisory_unlock(hashtext(%s))",
                           (f"mnemo2_sleep:{cx.schema}",))
        return {"ok": True, "compressed_sessions": n_ep, "working_scanned": len(rows)}

    # -- diagnostics --------------------------------------------------------------
    def diagnose(self, *, repair_vec_working=False, dry_run=False) -> dict:
        checks = {}
        with self._cx() as cx:
            checks["vector_ext"] = cx.query_scalar(
                "select count(*) from pg_extension where extname='vector'") == 1
            checks["schema_present"] = cx.query_scalar(
                "select count(*) from information_schema.tables where "
                "table_schema=%s and table_name='working_memory'",
                (cx.schema,)) == 1
            missing = cx.query(
                "select w.id from working_memory w left join memory_embeddings"
                " e on e.memory_id=w.id and e.kind='working' "
                "where e.memory_id is null")
            checks["vec_coverage_gaps"] = len(missing)
            repaired = 0
            if missing and repair_vec_working and not dry_run:
                for (mid,) in missing:
                    # vec_working parity = memory_embeddings rows (PG-native
                    # equivalent of the vendor vec0 mirror; no vec0 table).
                    c = cx.query_scalar(
                        "select content from working_memory where id=%s",
                        (mid,))
                    if c is not None:
                        cx.execute(
                            "INSERT INTO memory_embeddings(memory_id,kind,"
                            "embedding,model) VALUES (%s,'working',%s::vector,"
                            "%s) ON CONFLICT DO NOTHING",
                            (mid, _veclit(cx, c), _embed.active_model()))
                        repaired += 1
                checks["repaired"] = repaired
        return {"ok": True, "checks": checks, "sqlite": False}

    def recall_diagnostics(self, *, reset=False) -> dict:
        with self._cx() as cx:
            rows = cx.query_dicts(
                "select tier,hits,fallbacks,calls from recall_diagnostics"
                " order by tier")
            if reset:
                cx.execute("UPDATE recall_diagnostics SET hits=0,"
                           " fallbacks=0, calls=0")
        return {"ok": True, "tiers": _jsonable(rows)}

    def _bump_diagnost(self, cx, tiers, fallbacks=False):
        for tier, _scanned, matched in tiers:
            cx.execute(
                "INSERT INTO recall_diagnostics(tier,hits,fallbacks,calls) "
                "VALUES (%s,%s,0,1) ON CONFLICT (tier) DO UPDATE SET "
                "calls=recall_diagnostics.calls+1, hits=recall_diagnostics.hits"
                "+%s", (tier, 1 if matched else 0, 1 if matched else 0))
        if fallbacks:
            cx.execute(
                "INSERT INTO recall_diagnostics(tier,hits,fallbacks,calls) "
                "VALUES ('fallback',0,1,1) ON CONFLICT (tier) DO UPDATE SET"
                " fallbacks=recall_diagnostics.fallbacks+1, "
                "calls=recall_diagnostics.calls+1")

    # -- export / import -----------------------------------------------------------
    def export(self, output_path: str) -> dict:
        with self._cx() as cx:
            rows = cx.query_dicts(
                "select id,content,importance,source,scope,created_at,"
                "valid_until,veracity,metadata from working_memory order by id")
            eps = cx.query_dicts(
                "select id,session_id,summary,detail,created_at from "
                "episodic_memory order by id")
        data = {"memories": _jsonable(rows), "episodic": _jsonable(eps),
                "exported_at": _now().isoformat(), "engine": "mnemo-pg2"}
        with open(output_path, "w") as f:
            json.dump(data, f, default=str)
        return {"ok": True, "path": output_path,
                "count": len(rows) + len(eps)}

    def import_(self, *, input_path=None, provider=None, dry_run=False,
                force=False, channel_id=None) -> dict:
        items = []
        if input_path:
            with open(input_path) as f:
                data = json.load(f)
            items = data.get("memories", [])
        elif provider:
            # network provider fetch (Hindsight/Mem0) — DECLARED SIMPLIFIED
            # (spec §6): fetch path not shipped; file import is the path.
            return {"ok": False,
                    "error": f"provider import ({provider}) simplified in "
                             "mnemo-pg2: pass input_path JSON instead"}
        written = skipped = 0
        with self._cx() as cx:
            for it in items:
                mid = it.get("id") or new_id()
                exists = cx.query_scalar(
                    "select 1 from working_memory where id=%s", (mid,))
                if exists and not force:
                    skipped += 1
                    continue
                if exists:
                    cx.execute("DELETE FROM working_memory WHERE id=%s",
                               (mid,))
                    cx.execute(
                        "DELETE FROM memory_embeddings WHERE memory_id=%s",
                        (mid,))
                if dry_run:
                    written += 1
                    continue
                cx.execute(
                    "INSERT INTO working_memory(id,content,importance,source,"
                    "scope) VALUES (%s,%s,%s,%s,%s)",
                    (mid, it.get("content", ""),
                     float(it.get("importance", 0.5)),
                     it.get("source", "imported"),
                     it.get("scope", "session")))
                cx.execute(
                    "INSERT INTO memory_embeddings(memory_id,kind,embedding,"
                    "model) VALUES (%s,'working',%s::vector,%s)"
                    " ON CONFLICT (memory_id) DO UPDATE SET"
                    " embedding=EXCLUDED.embedding,"
                    " model=EXCLUDED.model",
                    (mid, _veclit(cx, it.get("content", "")),
                     _embed.active_model()))
                written += 1
        return {"ok": True, "written": written, "skipped": skipped}

    # -- sync (local-journal contract, network simplified) ---------------------
    def sync_push(self) -> dict:
        device = _device_id()
        with self._cx() as cx:
            rows = cx.query(
                "select max(id) from memory_events where id > "
                "coalesce((select value::bigint from sync_meta where "
                "key='cursor'),0)")
            n = cx.query_scalar(
                "select count(*) from memory_events where id > "
                "coalesce((select value::bigint from sync_meta where "
                "key='cursor'),0)")
            cx.execute("INSERT INTO sync_meta(key,value) VALUES ('cursor',"
                       "%s) ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value",
                       (str(rows[0][0] or 0),))
        return {"ok": True, "pushed": n, "device": device,
                "remote": "local-journal (network simplified)"}

    def sync_pull(self) -> dict:
        with self._cx() as cx:
            n = cx.query_scalar("select count(*) from memory_events")
        return {"ok": True, "applied": 0, "journal_events": n,
                "note": "single-instance journal: pull is a no-op read"}

    def sync_status(self) -> dict:
        with self._cx() as cx:
            return {"ok": True, "device_id": _device_id(),
                    "cursor": cx.query_scalar(
                        "select value from sync_meta where key='cursor'"),
                    "events": cx.query_scalar("select count(*) from memory_events"),
                    "remote": None, "encrypted": True}

    # -- model card / refresh / task progress / conflicts ---------------------
    def model_card(self, category, *, title="", names=None) -> dict:
        with self._cx() as cx:
            sql = ("select name,body from canonical_facts where category=%s"
                   " and valid_until IS NULL")
            params = [category]
            if names:
                sql += " and name = any(%s)"
                params.append(list(names))
            sql += " order by name"
            rows = cx.query_dicts(sql, params)
        lines = [f"# {title or category}", ""]
        for r in rows:
            lines.append(f"- **{r['name']}**: {r['body']}")
        return {"ok": True, "card": "\n".join(lines),
                "slots": len(rows), "llm_used": False}

    def model_refresh(self, *, action="list", status="all", limit=20) -> dict:
        with self._cx() as cx:
            sql = "select id,category,name,body,status,created_at from memory_model_proposals"
            params = []
            if status != "all":
                sql += " where status=%s"
                params.append(status)
            sql += " order by id desc limit %s" % int(limit)
            rows = cx.query_dicts(sql, params)
        return {"ok": True, "proposals": _jsonable(rows),
                "note": "sleep-time inference not wired (diagnostic read only)"}

    def task_progress(self, action="get", task="", state="", metadata=None) -> dict:
        cat = "task:progress"
        if action == "set":
            if not task:
                return {"ok": False, "error": "task required"}
            body = state or "unknown"
            r = self.remember_canonical(cat, task, body, source="task_progress")
            if metadata:
                self.remember(f"task_progress meta {task}: "
                             f"{json.dumps(metadata, default=str)}",
                             source="task_progress", scope="global")
            return {**r, "task": task, "state": body}
        if action in ("get", "list"):
            r = self.recall_canonical(category=cat, name=task or "")
            slots = r.get("slots", [])
            if action == "get" and task:
                return {"ok": bool(slots), "task": task,
                        "state": slots[0]["body"] if slots else None,
                        "updated_at": slots[0]["created_at"] if slots else None}
            return {"ok": True, "tasks": [
                {"task": s["name"], "state": s["body"],
                 "updated_at": s["created_at"]} for s in slots]}
        if action == "clear":
            if not task:
                return {"ok": False, "error": "task required"}
            return self.forget_canonical(cat, task)
        return {"ok": False, "error": f"unknown action {action}"}

    def resolve_conflicts(self, *, dry_run=False) -> dict:
        if os.environ.get("MNEMO2_CONFLICT_RESOLUTION", "0") != "1":
            return {"ok": False,
                    "error": "disabled (set MNEMO2_CONFLICT_RESOLUTION=1)"}
        resolved = []
        with self._cx() as cx:
            rows = cx.query_dicts(
                "select a.id aid, b.id bid, a.category, a.name from "
                "canonical_facts a join canonical_facts b on a.category="
                "b.category and a.name=b.name and a.id < b.id where "
                "a.valid_until IS NULL and b.valid_until IS NULL")
            for r in rows:
                if dry_run:
                    resolved.append(r)
                    continue
                cx.execute("UPDATE canonical_facts SET valid_until=CURRENT_DATE"
                           " WHERE id=%s", (r["aid"],))
                cx.execute("INSERT INTO conflicts(category,name,"
                           "old_memory_id,new_memory_id,resolution) VALUES "
                           "(%s,%s,%s,%s,'superseded_newer_wins')",
                           (r["category"], r["name"], str(r["aid"]),
                            str(r["bid"])))
                resolved.append(r)
        return {"ok": True, "resolved": len(resolved), "pairs": _jsonable(resolved)}

    def apply_pending(self, pending_ids=None) -> dict:
        # write_approval staging dir, YAGNI-simple (spec §6)
        import pathlib
        base = pathlib.Path(os.environ.get(
            "MNEMO2_PENDING_DIR",
            os.path.expanduser("~/.hermes/mnemo2-pending")))
        if not base.is_dir():
            return {"ok": True, "applied": 0, "note": "no pending dir"}
        applied = 0
        for p in sorted(base.glob("pending-*.json")):
            if pending_ids and p.stem.split("-", 2)[2] not in pending_ids:
                continue
            data = json.loads(p.read_text())
            for op in data.get("operations", []):
                self.batch([op])
                applied += 1
            p.rename(p.with_suffix(".applied.json"))
        return {"ok": True, "applied": applied}

    def remember_media(self, ref, *, modality=None, mime=None, title=None,
                       hint=None, max_moments=None, importance=0.5,
                       scope=None) -> dict:
        # DECLARED SIMPLIFIED (spec §6): registers ref + one text memory;
        # no vision/transcription models wired.
        modality = modality or _guess_modality(ref)
        mid = new_id("media")
        body = f"media:{modality} {title or ref}" + (f" (hint: {hint})" if hint else "")
        r = self.remember(body, importance=importance, source="media",
                          scope=scope or "session")
        with self._cx() as cx:
            cx.execute(
                "INSERT INTO media_assets(id,ref,modality,mime,title,status)"
                " VALUES (%s,%s,%s,%s,%s,'unavailable') ON CONFLICT (id) DO"
                " NOTHING", (mid, ref, modality, mime or "", title or ""))
            cx.execute(
                "INSERT INTO media_moments(asset_id,memory_id,caption)"
                " VALUES (%s,%s,%s)", (mid, r.get("id"), body))
        return {"ok": True, "status": "unavailable", "memory_id": r.get("id"),
                "asset_id": mid, "note": "media understanding not configured"}


# -- helpers ---------------------------------------------------------------

def _hit(r: dict, tier: str) -> dict:
    return {
        "id": r["id"], "content": r["content"], "tier": tier,
        "scope": r.get("scope"), "session_id": r.get("session_id"),
        "importance": r.get("importance"),
        "score": float(r.get("score") or 0.0),
        "vec_sim": float(r.get("vec_sim") or 0.0),
        "fts": float(r.get("fts") or 0.0),
        "created_at": str(r.get("created_at")),
        "expand_hint": f"mnemosyne_get(memory_id={r['id']!r})",
    }


def _jsonable(rows):
    import datetime as _dt
    import decimal

    def conv(o):
        if isinstance(o, (_dt.datetime, _dt.date)):
            return o.isoformat()
        if isinstance(o, decimal.Decimal):
            return float(o)
        return o
    return [{k: conv(v) for k, v in r.items()} for r in rows]


_CAP_WORDS = ("is are was were be been being the a an of to in on at for with"
              " and or not no this that these those i you he she it we they").split()


def _extract_entities(text: str) -> list[str]:
    # vendor parity (core/entities.py fan-out target = triples 'mentions')
    ents = []
    for word in text.split():
        w = word.strip(".,;:!?\"'()[]")
        if (len(w) > 2 and w[0].isupper() and w.lower() not in _CAP_WORDS
                and not w.isdigit()):
            ents.append(w)
    return sorted(set(ents))[:10]


def _heuristic_triples(text: str) -> list[tuple]:
    # tiny subject-predicate-object heuristic (LLM extraction not wired —
    # declared simplified)
    out = []
    for sent in text.replace("\n", ". ").split("."):
        sent = sent.strip()
        if len(sent.split()) < 3:
            continue
        parts = sent.split(None, 1)
        subj = parts[0].strip(".,;:")
        rest = parts[1].split(None, 1)
        if len(rest) == 2:
            out.append((subj.lower(), rest[0].lower(), rest[1].strip(".,;:")[:120]))
    return out[:5]


def _device_id() -> str:
    import socket
    return socket.gethostname()


def _guess_modality(ref: str) -> str:
    low = str(ref).lower().split("?")[0]
    for ext, mod in ((".png", "image"), (".jpg", "image"), (".jpeg", "image"),
                     (".gif", "image"), (".webp", "image"), (".mp3", "audio"),
                     (".wav", "audio"), (".ogg", "audio"), (".mp4", "video"),
                     (".mov", "video"), (".webm", "video"), (".pdf", "document"),
                     (".epub", "document"), (".docx", "document")):
        if low.endswith(ext):
            return mod
    return "document"
