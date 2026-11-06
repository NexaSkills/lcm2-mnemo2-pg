# Phase 5 spec — semantic embeddings (fastembed, PG-native) — 2026-11-06

Goal: vendor-parity local embeddings (fastembed ONNX, small model) for
paraphrase recall. HARD constraints (Jan): NEVER the main/active LLM; NEVER
sqlite; PG-native storage (pgvector + HNSW); hashed stays as offline fallback.

## Verified groundwork (not assumed)
- Vendor default: BAAI/bge-small-en-v1.5, local fastembed, cache_dir config,
  embed()/embed_query() lanes with env prefixes; API mode separate (source read).
- Vendor dedupe = exact-string only -> no semantic-dedupe to port.
- beez ACTIVE env (facts.json): fastembed imports OK; model verified live:
  384-dim, cold load 13.5 s (cached ONNX under repo models/fastembed/),
  15 ms/embed.
- pgvector 0.8.7 on the instance -> HNSW available. No ANN index exists.
- memory_embeddings.embedding is vector(256) (hashed); real rows all 256.

## Design
- Provider chain selected by MNEMO2_EMBED_MODEL: "" (auto) -> fastembed if
  importable+loadable, else hashed; "fastembed[:model]"; "hashed"; api when
  MNEMO2_EMBED_API_URL set (unchanged). Explicit model string = fastembed model.
- Cache dir MNEMO2_EMBED_CACHE (default <repo>/models/fastembed).
- Lanes: embed_text() = document lane (all write sites, unchanged signature);
  embed_query() = query lane (recall only); prefixes via
  MNEMO2_EMBED_DOC_PREFIX / MNEMO2_EMBED_QUERY_PREFIX (empty defaults; bge
  needs none, e5-style models set them).
- model_tag(): fastembed:<model> | hashed256 | api:<model> written into
  memory_embeddings.model at EVERY write site (fixes legacy blank tags).
- Dim integrity (PG-native, vendor's vec0 lesson): ddl VEC_DIM from
  MNEMO2_EMBED_DIM (default 384 = fastembed model); engine store helper reads
  the column dim once per schema (pg_attribute atttypmod, cached in-process)
  and SKIPS vector writes on mismatch (fail-soft, meta flag once) — never
  truncates/pads.
- Migration scripts/embed_migrate.sh [schema|all]: NULL embeddings ->
  ALTER COLUMN TYPE vector(target) -> batched re-embed from stored text
  (working.content, episodic.summary, canonical.body; lossless design
  guarantees source text) with model tags -> HNSW cosine index per schema.
  Resumable (skips rows already tagged current model).
- No-LLM guarantee: embed.py imports stdlib + fastembed only; never llm.py,
  never proxy env (grep test). Model load warm thread already exists
  (P4-B hook now calls embed.warm()).
- Test suite determinism: conftest pins MNEMO2_EMBED_MODEL=hashed unless a
  test opts in; S5 semantic tests opt into fastembed explicitly.

## Acceptance (beez only this round)
1. Suite green (fastembed tests included) + 8-profile E2E green.
2. psql: real schemas embeddings dim=384, model tags set, HNSW index present.
3. Paraphrase probe: zero-word-overlap pair recalled via tool lane.
4. Guard tests: no-LLM import graph; dim-mismatch fail-soft; migration resume.
5. Gateway-surface proof after restart; template deployment ONLY on Jan OK.
