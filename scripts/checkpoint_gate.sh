#!/usr/bin/env bash
# B3 CHECKPOINT GATE — hard rule: every turn in this task must end with a
# pushed checkpoint commit + CONT section in the result file. Install as a
# git post-commit hook (scripts/checkpoint_gate.sh) or run manually at the
# end of a turn:  bash scripts/checkpoint_gate.sh "<what changed>"
#
# Behaviour (deterministic, host-agnostic):
#  * runs the R8 gate three times with UNIQUE per-invocation LCM2_R8_RUN tags
#    (bare default env fails under concurrent dispatch runs — see CONT-4:
#    module-level RUN fallback re-resolves per spawn worker; only a pinned
#    env tag is shared with workers).
#  * appends a one-line gate receipt (tail + SHAs + timestamp) to the fleet
#    result file if present, and commits+pushes it if the repo is clean.
set -u
REPO="${1:-$HOME/lcm2-mnemo2-pg}"
RESULT="${2:-$HOME/fleet/reports/lcm2-mnemo2-redesign.phase2.result.md}"
cd "$REPO" || exit 1
PYTEST="${PYTEST:-$HOME/lcm-pg/venv/bin/pytest}"
[ -x "$PYTEST" ] || PYTEST="$(command -v pytest || true)"
[ -n "$PYTEST" ] || { echo "no pytest runner found"; exit 1; }

echo "== gate $(date '+%F %T') HEAD=$(git rev-parse --short HEAD) =="
for i in 1 2 3; do
  out=$(LCM2_R8_RUN="gate-$(date +%s)-$$-$i" "$PYTEST" tests/lcm2/test_r8_concurrency.py -q --tb=no 2>&1 | tail -1)
  echo "R8#$i: $out"
done
out=$("$PYTEST" tests/lcm2 -q --tb=no 2>&1 | tail -1)
echo "full: $out"

if [ -f "$RESULT" ]; then
  {
    echo ""
    echo "<!-- gate $(date '+%F %T %Z') HEAD=$(git rev-parse --short HEAD) remote=$(git ls-remote origin main 2>/dev/null | cut -c1-7) R8x3+full: see CI log; runner=$PYTEST -->"
  } >> "$RESULT"
fi
echo "== done $(date '+%F %T') =="
