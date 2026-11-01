#!/usr/bin/env bash
# LIVE CONCURRENCY PROOF (chunk C): two agents write for-real into their own
# schemas on lcm2db SIMULTANEOUSLY, each as a real hermes one-shot process.
# lcm2agent -> schema lcm2_lcm2agent (via lcm2-pg plugin hooks)
# mnemo2agent -> schema lcm2_mnemo2agent (whatever its plugin chain is)
STAMP=$(date +%H%M%S)
export LCM2_PG_DSN="postgresql:///lcm2db?host=/home/mybuddy/lcm2-mnemo2-pg/pg/run&port=54331&user=mnemo201"
export LCM_CONTEXT_THRESHOLD=0.01
cd /home/mybuddy

psql "host=/home/mybuddy/lcm2-mnemo2-pg/pg/run port=54331 user=mnemo201 dbname=lcm2db" \
  -c "select 'lcm2_lcm2agent msgs BEFORE='||count(*) from lcm2_lcm2agent.messages" \
  -c "select 'lcm2_mnemo2agent msgs BEFORE='||count(*) from lcm2_mnemo2agent.messages" \
  -c "select 'lcm2_lcm2agent nodes BEFORE='||count(*) from lcm2_lcm2agent.summary_nodes" \
  -c "select 'lcm2_mnemo2agent nodes BEFORE='||count(*) from lcm2_mnemo2agent.summary_nodes"

# launch BOTH agents simultaneously (16 real turns each)
( for i in $(seq 1 16); do
    timeout 120 hermes -p lcm2agent -z "P2C-LIVE-CONC-A-$STAMP turn $i" >/dev/null 2>&1
  done ) &
PA=$!
( for i in $(seq 1 16); do
    timeout 120 hermes -p mnemo2agent -z "P2C-LIVE-CONC-B-$STAMP turn $i" >/dev/null 2>&1
  done ) &
PB=$!
wait $PA $PB
echo "both agents finished $(date '+%H:%M:%S')"

psql "host=/home/mybuddy/lcm2-mnemo2-pg/pg/run port=54331 user=mnemo201 dbname=lcm2db" \
  -c "select 'lcm2_lcm2agent msgs AFTER='||count(*) from lcm2_lcm2agent.messages" \
  -c "select 'lcm2_mnemo2agent msgs AFTER='||count(*) from lcm2_mnemo2agent.messages" \
  -c "select 'lcm2_lcm2agent nodes AFTER='||count(*) from lcm2_lcm2agent.summary_nodes" \
  -c "select 'lcm2_mnemo2agent nodes AFTER='||count(*) from lcm2_mnemo2agent.summary_nodes" \
  -c "select 'A_tagged_msgs='||count(*) from lcm2_lcm2agent.messages where content like 'P2C-LIVE-CONC-A-$STAMP%'" \
  -c "select 'B_tagged_msgs='||count(*) from lcm2_mnemo2agent.messages where content like 'P2C-LIVE-CONC-B-$STAMP%'" \
  -c "select 'A_nodes_tagged='||count(*) from lcm2_lcm2agent.summary_nodes where summary like 'P2C-LIVE-CONC-A-$STAMP%' or summary like '%P2C-LIVE-CONC-A-$STAMP%'"
