#!/usr/bin/env bash
# embed_migrate.sh — Phase 5 (S5): bring memory_embeddings to the ACTIVE
# embedding provider's dimension, re-embed rows from the stored source text
# (lossless design guarantees the text exists), tag models, add HNSW cosine
# indexes. PG-native throughout; no sqlite, no LLM. Resumable: rows already
# tagged with the current model are skipped.
#   usage: embed_migrate.sh [schema ...]   (default: all mnemo2_* + shared)
set -eu
REPO="$(cd "$(dirname "$0")/.." && pwd)"
SCHEMAS=("$@")
PY=${PYTHON:-python3}

if [ ${#SCHEMAS[@]} -eq 0 ]; then
  mapfile -t SCHEMAS < <($PY - "$REPO" <<'PYEOF'
import sys, os
sys.path.insert(0, os.path.join(sys.argv[1], "plugins", "mnemo-pg2"))
import pgconn
with pgconn.connect(None, ddl=False) as cx:
    for r in cx.query_dicts(
            "SELECT nspname AS s FROM pg_namespace"
            " WHERE (nspname LIKE 'mnemo2%' OR nspname = 'shared')"
            " ORDER BY 1"):
        print(r["s"])
PYEOF
)
fi

$PY - "$REPO" "${SCHEMAS[@]}" <<'PYEOF'
import os, sys
repo = sys.argv[1]
schemas = sys.argv[2:]
sys.path.insert(0, os.path.join(repo, "plugins", "mnemo-pg2"))
import embed, pgconn

warm = embed.warm()
target = embed.active_dim() or int(os.environ.get("MNEMO2_EMBED_DIM", "256"))
tag = embed.active_model()
print(f"provider={embed.active_provider()} dim={target} tag={tag} "
      f"(warm={warm})")
if not warm and embed.active_provider() != "hashed":
    print("WARN fastembed unavailable; aborting before any schema change")
    sys.exit(0)

BATCH = 256
for sch in schemas:
    if not sch.replace("_", "").isalnum():
        continue
    with pgconn.connect(None, ddl=False) as cx:
        exists = cx.query_scalar(
            "SELECT to_regclass(%s) IS NOT NULL", (f"{sch}.memory_embeddings",))
        if not exists:
            print(f"{sch}: no memory_embeddings, skipped")
            continue
        cur = cx.query_scalar(
            "SELECT atttypmod FROM pg_attribute a JOIN pg_class c"
            " ON c.oid=a.attrelid JOIN pg_namespace n"
            " ON n.oid=c.relnamespace WHERE n.nspname=%s"
            " AND c.relname='memory_embeddings' AND a.attname='embedding'",
            (sch,))
        cur = int(cur or 0)
        # embedding must always be nullable: the dim guard writes NULL
        # (fail-soft) on provider/column mismatch (S5 invariant). Must
        # come BEFORE any NULL-out (constraint blocks the UPDATE first).
        cx.execute(f"ALTER TABLE {sch}.memory_embeddings"
                   " ALTER COLUMN embedding DROP NOT NULL")
        if cur != target:
            # views (shared_embeddings across every private schema) pin
            # the column type: drop, alter, rebuild via ddl templates
            deps = cx.query_dicts(
                "SELECT n2.nspname AS vsch, c.relname AS vname"
                " FROM pg_depend d JOIN pg_rewrite r ON r.oid = d.objid"
                " JOIN pg_class c ON c.oid = r.ev_class"
                " JOIN pg_namespace n2 ON n2.oid = c.relnamespace"
                " WHERE d.classid = 'pg_rewrite'::regclass"
                "  AND d.refobjid = %s::regclass AND c.relkind = 'v'",
                (f"{sch}.memory_embeddings",))
            for d in deps:
                cx.execute(f'DROP VIEW IF EXISTS "{d["vsch"]}"."{d["vname"]}"')
            cx.execute(f"UPDATE {sch}.memory_embeddings"
                       " SET embedding = NULL")
            cx.execute(f"ALTER TABLE {sch}.memory_embeddings"
                       f" ALTER COLUMN embedding TYPE vector({target})"
                       " USING NULL")
            import ddl as _ddl
            for d in deps:
                tmpl = {
                    "shared_embeddings": _ddl._SHARED_VIEWS[1],
                    "shared_memory": _ddl._SHARED_VIEWS[0],
                    "shared_canonical": _ddl._SHARED_VIEWS[2],
                }.get(d["vname"])
                if tmpl:
                    cx.execute(tmpl.replace("{SELF}", f'"{d["vsch"]}"')
                               .replace("{SH}", f'"{sch}"'))
                else:
                    print(f"  WARN dependent view {d['vsch']}.{d['vname']}"
                          " dropped but no rebuild template")
            print(f"{sch}: column {cur} -> vector({target}),"
                  f" {len(deps)} dependent view(s) rebuilt")
        # re-embed from source text (id, text, kind); sources built per
        # table existence (schemas may lack episodic/canonical tables)
        parts, pargs = [], []
        if cx.query_scalar("SELECT to_regclass(%s) IS NOT NULL",
                          (f"{sch}.working_memory",)):
            parts.append(
                f"SELECT w.id AS id, w.content AS txt, 'working' AS kind"
                f" FROM {sch}.working_memory w"
                f" LEFT JOIN {sch}.memory_embeddings e"
                "   ON e.memory_id=w.id AND e.model=%s"
                "  AND e.embedding IS NOT NULL"
                " WHERE w.content IS NOT NULL AND e.memory_id IS NULL")
            pargs.append(tag)
        if cx.query_scalar("SELECT to_regclass(%s) IS NOT NULL",
                          (f"{sch}.episodic_memory",)):
            parts.append(
                f"SELECT p.id AS id, coalesce(p.summary,'') || ' ' ||"
                f"  coalesce(p.detail,'') AS txt, 'episodic' AS kind"
                f" FROM {sch}.episodic_memory p"
                f" LEFT JOIN {sch}.memory_embeddings e"
                "   ON e.memory_id=p.id AND e.model=%s"
                "  AND e.embedding IS NOT NULL"
                " WHERE p.summary IS NOT NULL AND e.memory_id IS NULL")
            pargs.append(tag)
        if cx.query_scalar("SELECT to_regclass(%s) IS NOT NULL",
                          (f"{sch}.canonical_facts",)):
            parts.append(
                f"SELECT f.id::text AS id, f.body AS txt, 'canonical' AS kind"
                f" FROM {sch}.canonical_facts f"
                f" LEFT JOIN {sch}.memory_embeddings e"
                "   ON e.memory_id=f.id::text AND e.model=%s"
                "  AND e.embedding IS NOT NULL"
                " WHERE e.memory_id IS NULL")
            pargs.append(tag)
        rows = cx.query_dicts(" UNION ALL ".join(parts),
                              pargs) if parts else []
        done = 0
        for i in range(0, len(rows), BATCH):
            chunk = rows[i:i + BATCH]
            vecs = embed.embed_docs([r["txt"] or "" for r in chunk])
            if vecs is None:
                vecs = [embed.embed_text(r["txt"] or "") for r in chunk]
            args = [(r["id"], r["kind"], embed.to_pg_vector(v), tag)
                    for r, v in zip(chunk, vecs)
                    if len(v) == target]
            if args:
                cx.execute(
                    f"INSERT INTO {sch}.memory_embeddings"
                    " (memory_id,kind,embedding,model) VALUES"
                    " (unnest(%s::text[]),unnest(%s::text[]),"
                    " unnest(%s::vector[]),unnest(%s::text[]))"
                    " ON CONFLICT (memory_id) DO UPDATE SET"
                    " kind=EXCLUDED.kind, embedding=EXCLUDED.embedding,"
                    " model=EXCLUDED.model",
                    ([a[0] for a in args], [a[1] for a in args],
                     [a[2] for a in args], [a[3] for a in args]))
            done += len(args)
        # hygiene purge: embedding rows whose source text is gone
        # cannot ever be embedded (orphaned by delete-lane leaks pre-S5)
        orphans = [f"EXISTS (SELECT 1 FROM {sch}.working_memory w"
                   f" WHERE w.id = e.memory_id)"]
        if cx.query_scalar("SELECT to_regclass(%s) IS NOT NULL",
                           (f"{sch}.episodic_memory",)):
            orphans.append(f"EXISTS (SELECT 1 FROM {sch}.episodic_memory p"
                           f" WHERE p.id = e.memory_id)")
        if cx.query_scalar("SELECT to_regclass(%s) IS NOT NULL",
                           (f"{sch}.canonical_facts",)):
            orphans.append(f"EXISTS (SELECT 1 FROM {sch}.canonical_facts f"
                           f" WHERE f.id::text = e.memory_id)")
        purged = cx.execute(
            f"DELETE FROM {sch}.memory_embeddings e"
            " WHERE e.embedding IS NULL AND NOT ("
            + " OR ".join(orphans) + ")")
        cx.execute(f"CREATE INDEX IF NOT EXISTS me_hnsw ON"
                   f" {sch}.memory_embeddings USING hnsw"
                   " (embedding vector_cosine_ops)")
        try:
            cx.execute("INSERT INTO meta(key,value)"
                       " VALUES('embedding_model',%s)"
                       " ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value",
                       (tag,))
        except Exception:
            pass  # meta table exists only in private schemas
        nulls = cx.query_scalar(
            f"SELECT count(*) FROM {sch}.memory_embeddings"
            " WHERE embedding IS NULL")
        print(f"{sch}: embedded {done} rows,"
              f" purged {getattr(purged, 'rowcount', 0)} orphans,"
              f" {nulls} NULL remain, hnsw OK")
print("DONE")
PYEOF
