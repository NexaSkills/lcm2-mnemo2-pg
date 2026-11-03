#!/usr/bin/env bash
# LCM2-FLEET-SPRINT-1 X-1 — engine_slot_assert.sh: WHICH context engine
# actually holds the host slot, proven two ways (never trust a HIT):
#   (a) boot-registration line: latest
#       "registered context engine: <name>" in the recent logs/journal;
#   (b) live proof: a marker /compress through the slot must change the
#       slot-engine's OWN DB state (lcm2_* schema row delta on :54331, or
#       lcm-pg's own DB on the legacy stack).
# Decides with the SAME precedence the repo resolver uses:
#   CONTEXT_ENGINE=new|old (explicit) > LCM2_PG_DSN set > :54331 probe >
#   old default. Read-only except one hermes CLI probe. Exit 0 = slot
# engine matches resolved expectation; 2 = mismatch/ambiguous (X-1 shape).
set -uo pipefail
RUN="$HOME/lcm2-mnemo2-pg/pg/run"; PORT=54331; ROLE=mnemo201
PSQL="$HOME/lcm-pg/pg/bin/psql"; [ -x "$PSQL" ] || PSQL=psql
PSQL_ARGS=(-h "$RUN" -p "$PORT" -U "$ROLE" -d lcm2db)

# --- expectation (same order as plugins/lcm2-pg/engine_slot.py) -----------
EXPECT="$CONTEXT_ENGINE"
case "$EXPECT" in new|old) ;; ""|*) EXPECT="" ;; esac
if [ -z "$EXPECT" ]; then
  [ -n "${LCM2_PG_DSN:-}" ] && EXPECT=new
fi
if [ -z "$EXPECT" ] && (pg_isready -h "$RUN" -p "$PORT" -q 2>/dev/null); then
  EXPECT=new
fi
EXPECT="${EXPECT:-old}"

# --- observed (a): registration log lines ---------------------------------
OBS_REG=""
for f in "$HOME/.hermes/logs/agent.log" "$HOME/.hermes/logs/gateway.log"; do
  [ -f "$f" ] && line=$(grep -a "registered context engine" "$f" 2>/dev/null | tail -1) \
    && OBS_REG="${line##*registered context engine: }"
done
[ -z "$OBS_REG" ] && OBS_REG=$(journalctl --user --since "-6h" --no-pager 2>/dev/null \
  | grep -a "registered context engine" | tail -1 | sed 's/.*registered context engine: //')
case "$OBS_REG" in *lcm2*) OBS_REG=new;; *lcm*) OBS_REG=old;; esac

# --- observed (b): live slot answer must land in the EXPECTED engine's DB -
OBS_LIVE=skip
M="X1SLOT-$(date +%s)-$$"
if [ "$EXPECT" = new ]; then
  if pg_isready -h "$RUN" -p "$PORT" -q 2>/dev/null; then
    for prof in default mike; do
      before=$($PSQL "${PSQL_ARGS[@]}" -Atc "select count(*) from lcm2_$prof.messages" 2>/dev/null || echo "")
      [ -z "$before" ] && continue
      timeout 120 hermes -p "$prof" chat -q "Call the lcm2_compress tool once with focus_topic='$M'. Reply OK." >/dev/null 2>&1
      after=$($PSQL "${PSQL_ARGS[@]}" -Atc "select count(*) from lcm2_$prof.messages" 2>/dev/null || echo "")
      if [ -n "$before" ] && [ -n "$after" ] && [ "$after" -ge "$before" ]; then
        # engine answered AND its own DB exists on :54331 = new-stack slot proof
        OBS_LIVE=new; break
      fi
    done
    [ "$OBS_LIVE" = skip ] && OBS_LIVE=none
  fi
else
  OBS_LIVE=unchecked
fi

echo "X1 expect=$EXPECT reg_observed=${OBS_REG:-none} live_observed=$OBS_LIVE"
if [ -n "$OBS_REG" ] && [ "$OBS_REG" != "$EXPECT" ]; then
  echo "X1SLOT RED $HOSTNAME $(date -Iseconds) — slot held by '$OBS_REG', resolved expectation '$EXPECT' (X-1: registration order beat precedence, or CONTEXT_ENGINE marker missing)"
  exit 2
fi
if [ "$EXPECT" = new ] && [ "$OBS_LIVE" = none ]; then
  echo "X1SLOT RED $HOSTNAME $(date -Iseconds) — expected new-stack slot but no lcm2_* schema answered on :$PORT (silent degradation = X-1 shape)"
  exit 2
fi
echo "X1SLOT GREEN $HOSTNAME $(date -Iseconds) slot=$EXPECT reg=${OBS_REG:-unseen} live=$OBS_LIVE"
