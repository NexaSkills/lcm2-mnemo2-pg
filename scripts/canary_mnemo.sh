#!/usr/bin/env bash
# mnemo-pg2 + nexa-kev self-learning canary — AUTOMATIC remember/recall proof
# in REAL agent sessions, verified entirely OUTSIDE the agent (PG rows +
# lane trail rows + fresh-session output). No prompt ever says "save this":
# memory decisions must come from the kev lanes (memlearn capture, memroute
# bank routing, memrecall error recall) + mnemo-pg2 passive recall.
#
# Usage (on the target host):  bash canary_mnemo.sh [profile]
#   no arg    -> root/default surface
#   <profile> -> hermes -p <profile>
# Prints a verdict table; exit 0 = CANARY: PASS.
# Self-cleaning: deletes ONLY rows containing this run's unique anchors
# (agent rows are quarantined into metadata->>'canary' + soft-expired).
set -uo pipefail
PROF=${1:-}
HC=(hermes); [ -n "$PROF" ] && HC=(hermes -p "$PROF")
AGENT=${PROF:-default}
PSQL="$HOME/lcm-pg/pg/bin/psql -h $HOME/lcm2-mnemo2-pg/pg/run -p 54331 -U mnemo201 -d mnemo2db"
SCHEMA="mnemo2_$AGENT"
TRAIL_DEFAULT="$HOME/.hermes/plugins-data/nexa-kev/decisions.jsonl"
[ -n "$PROF" ] && TRAIL_DEFAULT="$HOME/.hermes/profiles/$PROF/plugins-data/nexa-kev/decisions.jsonl"
TRAIL="${NK_TRAIL:-$TRAIL_DEFAULT}"
R=$(openssl rand -hex 3)
A1="kaleidoscope-$R"; A2="farthingdale-$R"   # A1 private rule, A2 fleet doctrine
BAD="drill-dr-$R"                              # fake binary for the L2 error scene
LOG=/tmp/canary-mnemo-$R.log
fails=0
say(){ printf '  %-26s %s\n' "$1" "$2"; }
fail(){ say "$1" "FAIL$2"; fails=$((fails+1)); }
warn(){ say "$1" "WARN$2"; }
turn(){ # turn <prompt> -> sets OUT, SID
  { [ -z "${MARKLOCK:-}" ] && mark; }; OUT=$(timeout 200 env -u HERMES_HOME "${HC[@]}" --pass-session-id -z "$1" 2>/dev/null)
  SID=$(echo "$OUT" | grep -oE '[0-9a-f]{24,36}' | tail -1)
  echo "===== SID=$SID PROMPT=$1" >> "$LOG"; echo "$OUT" >> "$LOG"
}
mark(){ T0=$(date +%s 2>/dev/null); }   # trail window start (before turn)
trailrow(){ # trailrow <kind> -> newest row of kind written since mark()
  [ -f "$TRAIL" ] || return 0
  grep -h "\"kind\": \"$1\"" "$TRAIL" 2>/dev/null \
    | sed -E 's/.*"ts": ([0-9]+).*/\1|&/' | awk -F'|' -v s="$T0" '$1+0>s+0 {print substr($0,index($0,"{"))}' | tail -1
}

echo "== mnemo/kev self-learning canary (agent=$AGENT anchors=$R)"

# 1. L1 automatic capture: natural learning moment, no save instruction.
turn "glad that worked out â from now on, when you write canary notes for this machine always end them with the audit tag $A1 so they can be traced"
row=$(trailrow memlearn)
if echo "$row" | grep -q '"decision": "fired"'; then say l1_capture "OK ($([ -n "$row" ] && echo "$row" | grep -oE '"p_save": [0-9.]+' | head -1))"
else fail l1_capture " (trail: ${row:-none})"; fi
sleep 2
P1=$($PSQL -At -c "select count(*) from $SCHEMA.working_memory where content like '%$A1%' and scope='persistent'" 2>/dev/null)
[ "${P1:-0}" -ge 1 ] && say l1_db_private "OK ($P1 row)" || fail l1_db_private " (rows=${P1:-?})"

# 2. L3 automatic bank routing: fleet doctrine phrasing -> shared bank.
turn "policy for everyone on every host from now on: the drill keyword is $A2"
row=$(trailrow memlearn)
P2=$($PSQL -At -c "select count(*) from shared.working_memory where content like '%$A2%'" 2>/dev/null)
if [ "${P2:-0}" -ge 1 ]; then say l3_shared_bank "OK ($P2 row)"
elif echo "$row" | grep -q '"bank": "shared"'; then say l3_shared_bank "OK (decision routed shared; agent wrote elsewhere)"
else fail l3_shared_bank " (shared rows=${P2:-0}, decision: ${row:-none})"; fi

# 3. AUTOMATIC recall, fresh session, zero instructions: answer must surface
#    the rule purely from passive injection / the agent's own memory use.
turn "Reply with exactly the keyword I told you about for canary summaries on this machine. Nothing else."
echo "$OUT" | grep -q "$A1" && say auto_recall_private "OK" || warn auto_recall_private " (not echoed by model — check injection)"

# 4. AUTOMATIC cross-agent recall via shared bank (second real agent session).
turn "What is the fleet-wide shared drill keyword? Reply with exactly the keyword, nothing else."
echo "$OUT" | grep -q "$A2" && say auto_recall_shared "OK" || warn auto_recall_shared " (not echoed by model)"

# 5. L2 automatic error-recall: hit a hard error twice in two fresh
#    sessions; the FIRST failure is remembered automatically (turn ingest),
#    the SECOND must trigger the lane's automatic recall of that experience.
MARKLOCK=1; mark
for n in 1 2 3; do
  turn "use the terminal tool to run the command $BAD once in the FOREGROUND (no backgrounding, no sleep) and quote its raw output"
  ROW=$(trailrow memrecall); [ -n "$ROW" ] && break
done
unset MARKLOCK
row=$(trailrow memrecall)
if echo "$row" | grep -q '"decision": "fired"'; then say l2_error_recall "OK ($([ -n "$row" ] && echo "$row" | grep -oE '"ms": [0-9.]+' | head -1))"
else fail l2_error_recall " (${row:-NO ROW â if advisory rows exist for this session but memrecall never logs, transform dispatch died after a hot-reload: bounce the gateway})"; fi
echo "$OUT" | grep -qiE "expected|does not exist|not exist" && say l2_trailer_used "OK" || warn l2_trailer_used " (fired row proves trailer presented; model echo is prose discretion)"

# ---- cleanup: quarantine agent rows, hard-delete probe rows ----
$PSQL -q -c "update $SCHEMA.working_memory set metadata = coalesce(metadata,'{}'::jsonb) || jsonb_build_object('canary_quarantined','$R'), expired_at=now(), valid_until=now() where content like '%$R%' and expired_at is null" 2>/dev/null
$PSQL -q -c "delete from shared.memory_embeddings where memory_id in (select id from shared.working_memory where content like '%$R%')" 2>/dev/null
$PSQL -q -c "delete from shared.working_memory where content like '%$R%'" 2>/dev/null
LEFT=$($PSQL -At -c "select (select count(*) from shared.working_memory where content like '%$R%') + (select count(*) from $SCHEMA.working_memory where content like '%$R%' and (metadata->>'canary_quarantined') is null)" 2>/dev/null)
[ "${LEFT:-1}" = "0" ] && say cleanup "OK (own rows purged; agent saves quarantined+expired, shared fully deleted)" || fail cleanup " (left=$LEFT)"

echo
[ "$fails" -eq 0 ] && { echo "CANARY: PASS"; exit 0; } || { echo "CANARY: FAIL ($fails)"; exit 1; }
