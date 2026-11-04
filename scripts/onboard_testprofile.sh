#!/usr/bin/env bash
# onboard_testprofile.sh — materialise a boot/regression profile the suite
# can actually boot (C2RESID2S: test_boot_stress boots `hermes -p <p>
# chat -q`; a bare profile created by `hermes profile create` has NO LLM
# credential, and if a base config carries an UNEXPANDED ${VAR} key
# reference the boot dies rc=1 HTTP 401 "Virtual Key expected" — an
# env-configuration failure, not an import-poisoning crash, but the
# stress test fails on rc!=0 all the same).
#
# Idempotent, CLI-only for config (fleet rule: config.yaml via
# `hermes config set` ONLY), .env file-editable (the ONE file-editable
# surface). Copies ONLY the LLM routing pair from a donor profile that
# provably boots (default: mike); never secrets of any other kind,
# never any other config key.
#
# Usage: onboard_testprofile.sh <agent> [donor]   (donor default: mike)
# Exit 0 = profile boots `chat -q` rc=0 (probe runs with LCM2_PROBE=1);
# 2 = any leg red (names it).
set -uo pipefail
AGENT=${1:?usage: onboard_testprofile.sh <agent> [donor]}
DONOR=${2:-mike}
HB=${HERMES_BASE:-${HERMES_REAL_HOME:-$HOME}/.hermes}
PDIR="$HB/profiles/$AGENT"
PENV="$PDIR/.env"
DENV="$HB/profiles/$DONOR/.env"

# 1) profile exists (create; a stub dir without identity must move aside)
if [ ! -f "$PDIR/profile.yaml" ] && [ ! -f "$PDIR/config.yaml" ]; then
  if [ -d "$PDIR" ]; then
    mv "$PDIR" "$HB/profiles-retired/$AGENT-stub-$(date +%Y%m%d%H%M%S)" \
      || { echo "ONBOARDFAIL cannot retire stub $PDIR" >&2; exit 2; }
    mkdir -p "$HB/profiles-retired" 2>/dev/null || true
  fi
  hermes profile create "$AGENT" --description "$AGENT (lcm2 boot/regression profile)" --no-alias \
    || { echo "ONBOARDFAIL hermes profile create $AGENT" >&2; exit 2; }
fi
[ -d "$PDIR" ] || { echo "ONBOARDFAIL no profile dir after create" >&2; exit 2; }

# 2) model routing via CLI only (idempotent sets, values from the donor)
for k in model.default model.provider; do
  v=$(hermes ${DONOR:+-p "$DONOR"} config get "$k" 2>/dev/null | tr -d '\n')
  [ -n "$v" ] && hermes -p "$AGENT" config set "$k" "$v" >/dev/null 2>&1
done
bu=$(hermes -p "$DONOR" config get model.base_url 2>/dev/null | tr -d '\n')
[ -n "$bu" ] && hermes -p "$AGENT" config set model.base_url "$bu" >/dev/null 2>&1

# 3) the LLM credential PAIR only, file-editable .env (600, append-safe)
touch "$PENV"; chmod 600 "$PENV"
grep -hE '^(HERMES_CUSTOM_[A-Z0-9_]+_API_KEY|OPENAI_API_KEY)=' "$DENV" 2>/dev/null \
  | grep -v '\${' | while IFS= read -r line; do
      k=${line%%=*}
      grep -q "^${k}=" "$PENV" || printf '%s\n' "$line" >> "$PENV"
    done
grep -qE '^(HERMES_CUSTOM_[A-Z0-9_]+_API_KEY)=sk-' "$PENV" \
  || { echo "ONBOARDFAIL no literal sk- key reachable from donor $DONOR .env (never copy an unexpanded \${VAR})" >&2; exit 2; }

# 4) boot probe (the class this script exists to close)
if [ "${LCM2_PROBE:-1}" = 1 ]; then
  if timeout 180 hermes -p "$AGENT" chat -q "Reply PONG" >/dev/null 2>&1; then
    echo "ONBOARDGREEN $AGENT boots rc=0 (donor=$DONOR) $(date -Iseconds)"
    exit 0
  fi
  echo "ONBOARDFAIL $AGENT boot rc!=0 — run manually: hermes -p $AGENT chat -q 'Reply PONG'" >&2
  exit 2
fi
echo "ONBOARDDONE $AGENT (probe skipped LCM2_PROBE=0)"
