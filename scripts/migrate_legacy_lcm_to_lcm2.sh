#!/usr/bin/env bash
# migrate_legacy_lcm_to_lcm2.sh — GAP2 (W3BEEZGAP 2026-11-04): the landed
# mechanism for copying legacy lcm-pg context DATA (:54329 cluster, schemas
# lcm_<p>, sqlite-era column shapes) into the new lcm2-pg schemas (:54331,
# lcm2_<p>). Cutover only ever did CONFIG cutover (cutover_profile.sh); the
# row-level old->new copy was never specified (beezfull QA C-F PARTIAL).
#
# Scope (YAGNI, honest): the two tables the engine actually reads for
# retrieval are messages + summary_nodes. Legacy column shapes differ
# (float epochs -> TIMESTAMPTZ via to_timestamp, int pinned -> boolean,
# text tool_calls/source_ids -> jsonb) — mapped explicitly below. All other
# legacy tables (metadata, lifecycle_state, rollups, embeddings) are
# engine-internal bookkeeping the NEW engine rebuilds; they are NOT copied
# (embedding vectors of a different model must never be smuggled across).
#
# Safety contract:
#   * NEVER writes to the source. Source connection runs default_transaction_read_only=on;
#     the script's own SQL to the source is SELECT-only (dblink pull).
#   * Destination is ONLY a THROWAWAY schema unless --execute is passed.
#     Default mode = dry-run: authors dst_lcm2_<p>__mig, copies, row-count
#     reconciles (src vs dst), reports, and DROPS the throwaway. Nothing
#     live is touched in dry-run; --execute renames dst_lcm2_<p>__mig over
#     lcm2_<p> atomically (the live schema is renamed to
#     lcm2_<p>__premig.<ts> first, never dropped).
#   * Requires dblink available in the NEW db (extension ships with the
#     instance); source credentials come from env, never hardcoded secrets.
#
# Usage:
#   migrate_legacy_lcm_to_lcm2.sh <profile> [--execute]
# Env:
#   LEGACY_DSN   libpq conn to the OLD cluster's lcm db
#                (default: host=~/lcm-pg/run port=54329 user=lcm01 dbname=lcm)
#   NEW_DSN      libpq conn to the NEW lcm2db
#                (default: host=~/lcm2-mnemo2-pg/pg/run port=54331 user=mnemo201 dbname=lcm2db)
#   SRC_SCHEMA   source schema override (default lcm_<profile>; use lcm_data
#                for an isolated rehearsal source)
# Exit: 0 reconciled, 1 row mismatch/fatal, 2 usage.
set -uo pipefail

PROFILE=${1:?usage: migrate_legacy_lcm_to_lcm2.sh <profile> [--execute]}
shift
# Schema naming mirrors pgconn.agent_schema EXACTLY: lcm2_<sanitized
# basename of the profile's HERMES_HOME> (profiles live under
# $HERMES_BASE/profiles/<p>; hyphens etc -> underscore, per _SCHEMA_RE).
P=$(echo "${PROFILE##*/}" | tr -c 'a-z0-9_' '_' | tr 'A-Z' 'a-z')
[[ $P =~ ^[a-z_][a-z0-9_]*$ ]] || { echo "FATAL: bad profile name" >&2; exit 2; }
EXECUTE=0
for a in "$@"; do
  case "$a" in
    --execute) EXECUTE=1 ;;
    *) echo "FATAL: unknown arg $a" >&2; exit 2 ;;
  esac
done

LEGACY_DSN=${LEGACY_DSN:-"host=$HOME/lcm-pg/run port=54329 user=lcm01 dbname=lcm"}
NEW_DSN=${NEW_DSN:-"host=$HOME/lcm2-mnemo2-pg/pg/run port=54331 user=mnemo201 dbname=lcm2db"}
SRC_SCHEMA=${SRC_SCHEMA:-lcm_${PROFILE}}

P=$(echo "${PROFILE##*/}" | tr -c 'a-z0-9_' '_' | tr 'A-Z' 'a-z')
[[ $P =~ ^[a-z_][a-z0-9_]*$ ]] || { echo "FATAL: bad profile name" >&2; exit 2; }
DST_LIVE="lcm2_${P}"
DST_TMP="lcm2_${P}__mig"
TS=$(date +%Y%m%d%H%M%S)

npsql() { psql "$NEW_DSN" -v ON_ERROR_STOP=1 -Atc "$1"; }
lpsql() { psql "${LEGACY_DSN%% options=*} options=-c\\ default_transaction_read_only=on" -v ON_ERROR_STOP=1 -Atc "$1"; }
fail() { echo "MIGRATE RED ($PROFILE): $*" >&2; exit 1; }

echo "== MIGRATE legacy lcm->$DST_LIVE for profile $PROFILE (execute=$EXECUTE) ts=$TS"
# 0. sources alive + source schema exists
lpsql "select 1" >/dev/null 2>&1 || fail "legacy cluster unreachable via LEGACY_DSN (start it first; it is NOT started by this script — operator owns the cluster)"
lpsql "select 1 from pg_namespace where nspname='$SRC_SCHEMA'" | grep -q 1 || fail "source schema $SRC_SCHEMA absent on legacy cluster"
npsql "select 1" >/dev/null 2>&1 || fail "new cluster unreachable via NEW_DSN"
# dblink is an OPTIONAL accelerator: used when present, else the row pull
# falls back to a client-side COPY pipe (source SELECTs, dest COPYs — still
# SELECT-only on the source, zero server-side privileges needed there).
HAVE_DBLINK=$(npsql "select 1 from pg_extension where extname='dblink'" | head -1)
[[ -n "$HAVE_DBLINK" ]] || HAVE_DBLINK=$(npsql "create extension if not exists dblink" 2>/dev/null && echo 1)
[[ -n "$HAVE_DBLINK" ]] && echo "   dblink: server-side pull available" || echo "   dblink: absent -> client-side COPY pipe (still SELECT-only on source)"
[[ -n "$HAVE_DBLINK" ]] || true

# 1. source row counts (read-only)
SRC_MSG=$(lpsql "select count(*) from \"$SRC_SCHEMA\".messages") || fail "cannot read $SRC_SCHEMA.messages"
SRC_NODE=$(lpsql "select count(*) from \"$SRC_SCHEMA\".summary_nodes") || fail "cannot read $SRC_SCHEMA.summary_nodes"
echo "   source: $SRC_MSG messages / $SRC_NODE summary_nodes"

# 2. throwaway destination schema mirroring the NEW engine DDL
npsql "drop schema if exists $DST_TMP cascade; create schema $DST_TMP" || fail "cannot author $DST_TMP"
# newest-side DDL = this repo's own ddl.py applied under the temp schema
# (run via a landed helper so the migration shell stays quoting-free)
REPO_DIR=${REPO_DIR:-$(cd "$(dirname "$0")/.." && pwd)}
python3 "$REPO_DIR/scripts/_migrate_apply_ddl.py" "$NEW_DSN" "$DST_TMP" \
  || fail "engine DDL apply failed under $DST_TMP"

# 3. pull rows (dblink server-side when available, else client-side pipe;
#    both paths are SELECT-only on the source)
if [[ -n "$HAVE_DBLINK" ]]; then
  DBLINK_SRC="${LEGACY_DSN%% options=*}"
  npsql "select dblink_connect('mig', '$DBLINK_SRC options=-c default_transaction_read_only=on')" \
    || fail "dblink_connect to legacy failed"
  trap 'npsql "select dblink_disconnect($DBLINK_Q$ mig$DBLINK_Q$)" >/dev/null 2>&1' EXIT
  DBLINK_Q="'"
  npsql "
insert into $DST_TMP.messages (session_id, source, conversation_id, role, content,
        tool_call_id, tool_calls, tool_name, timestamp, token_estimate, pinned,
        ingested_at, observed_at, observed_at_source)
select session_id, coalesce(source,''), coalesce(conversation_id,''), role, content,
       tool_call_id,
       case when tool_calls is null or (tool_calls not like '[%' and tool_calls not like '{%')
            then null else tool_calls::jsonb end,
       tool_name,
       to_timestamp(timestamp), token_estimate, pinned::boolean,
       to_timestamp(ingested_at), to_timestamp(observed_at), observed_at_source
from dblink('mig', 'select * from \"$SRC_SCHEMA\".messages') as m(
  store_id bigint, session_id text, source text, conversation_id text, role text,
  content text, tool_call_id text, tool_calls text, tool_name text, timestamp double precision,
  token_estimate int, pinned int, ingested_at double precision, observed_at double precision,
  observed_at_source text, content_tsv tsvector);
insert into $DST_TMP.summary_nodes (session_id, depth, summary, token_count,
        source_token_count, source_ids, source_type, created_at, earliest_at, latest_at, expand_hint)
select session_id, depth, summary, token_count, source_token_count,
       case when source_ids is null or (source_ids not like '[%' and source_ids not like '{%')
            then '[]'::jsonb else source_ids::jsonb end,
       coalesce(source_type,'messages'),
       to_timestamp(created_at), to_timestamp(earliest_at), to_timestamp(latest_at),
       coalesce(expand_hint,'')
from dblink('mig', 'select * from \"$SRC_SCHEMA\".summary_nodes') as n(
  node_id bigint, session_id text, depth int, summary text, token_count int,
  source_token_count int, source_ids text, source_type text, created_at double precision,
  earliest_at double precision, latest_at double precision, expand_hint text, summary_tsv tsvector);
" || fail "dblink pull/insert failed"
  npsql "select dblink_disconnect('mig')" >/dev/null 2>&1
  trap - EXIT
else
  for t in messages summary_nodes; do
    echo "   pipe: $t"
    python3 "$REPO_DIR/scripts/_migrate_copy_rows.py" \
      "$LEGACY_DSN" "$NEW_DSN" "$SRC_SCHEMA" "$DST_TMP" "$t" \
      || fail "client-side pipe failed on $t"
  done
fi

# 4. reconcile
DST_MSG=$(npsql "select count(*) from $DST_TMP.messages")
DST_NODE=$(npsql "select count(*) from $DST_TMP.summary_nodes")
echo "   dest($DST_TMP): $DST_MSG messages / $DST_NODE summary_nodes"
[[ "$DST_MSG" == "$SRC_MSG" && "$DST_NODE" == "$SRC_NODE" ]] \
  || fail "row-count reconcile FAILED (src $SRC_MSG/$SRC_NODE vs dst $DST_MSG/$DST_NODE)"

if [[ $EXECUTE == 1 ]]; then
  npsql "
    alter schema $DST_LIVE rename to ${DST_LIVE}__premig.$TS;
    alter schema $DST_TMP rename to $DST_LIVE;
  " || fail "rename swap failed (premig schema ${DST_LIVE}__premig.$TS holds the old data; manual rollback: rename back)"
  echo "   EXECUTED: live schema is now migrated; previous live preserved as ${DST_LIVE}__premig.$TS"
else
  npsql "drop schema $DST_TMP cascade" || fail "cannot drop throwaway $DST_TMP"
  echo "   DRY-RUN OK: throwaway dropped, nothing live touched. Re-run with --execute to swap in."
fi
echo "MIGRATE GREEN ($PROFILE): reconciled $SRC_MSG msgs + $SRC_NODE nodes"
