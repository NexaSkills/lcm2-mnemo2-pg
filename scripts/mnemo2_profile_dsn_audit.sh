#!/usr/bin/env bash
# mnemo2_profile_dsn_audit.sh — W3TKT-MIKE-1220 #5 (fail-closed audit leg).
# Assert: every profile the HOST considers live (named_profile_is_live:
# an existing dir with an identity marker, not tombstoned — the SAME
# predicate the gateway uses to serve profiles) carries MNEMO2_PG_DSN in
# its own .env. A routed turn for a profile WITHOUT that line runs on the
# composed default DSN, and every step of the absent-config banking class
# (host fall-through to the launch home) then has no profile-true DSN to
# lean on. Names each offender as file:line-free (KEY missing) — an
# operator action item.
#
# A marker-less profiles/<p>/ dir is NOT served (host predicate) and is
# reported as UNSERVED informationally, never as an audit failure: that
# class is fixed host-side (create the profile properly / delete the
# ghost), not by any DSN line — see the W3TKT #5 CLOSE/BLOCKED row.
#
# Usage: mnemo2_profile_dsn_audit.sh [--base <hermes-base>]
# Exit 0 = every served profile's .env carries MNEMO2_PG_DSN; 1 = offenders.
set -uo pipefail
HB=${HERMES_BASE:-${HERMES_REAL_HOME:-$HOME}/.hermes}
while [ $# -gt 0 ]; do
  case "$1" in
    --base) HB=$2; shift 2 ;;
    *) echo "usage: mnemo2_profile_dsn_audit.sh [--base <hermes-base>]" >&2; exit 2 ;;
  esac
done

# Host's OWN identity markers (hermes_constants._PROFILE_IDENTITY_MARKERS
# — kept as a local copy because this audit must run from any shell,
# including without the hermes interpreter importable).
MARKERS=(config.yaml .env SOUL.md profile.yaml auth.json state.db)

FAIL=0
SHOWN=0
for d in "$HB"/profiles/*/; do
  name=$(basename "$d")
  [ "$name" = "default" ] && continue
  case "$name" in .*) continue ;; esac
  live=0
  for m in "${MARKERS[@]}"; do
    # is_file OR dangling symlink = identity claim (matches the host rule)
    [ -e "$d$m" ] || [ -L "$d$m" ] || continue
    live=1; break
  done
  if [ "$live" = 0 ]; then
    echo "AUDIT unserved (marker-less ghost dir, host never serves it — fix host-side): $d"
    continue
  fi
  SHOWN=$((SHOWN+1))
  if grep -q '^MNEMO2_PG_DSN=' "$d.env" 2>/dev/null; then
    :  # profile-true DSN present
  else
    echo "AUDIT FAIL served profile lacks MNEMO2_PG_DSN in its .env: $d.env"
    FAIL=1
  fi
done
# the root/default home itself (launch profile) reads the root .env
if grep -q '^MNEMO2_PG_DSN=' "$HB/.env" 2>/dev/null; then
  :
else
  echo "AUDIT note: root .env lacks MNEMO2_PG_DSN (launch-profile default; onboard_profile.sh authors it per profile): $HB/.env"
fi
[ "$FAIL" = 0 ] && { echo "MNEMO2_PROFILE_DSN_AUDIT OK ($SHOWN served profile(s) checked)"; exit 0; }
echo "MNEMO2_PROFILE_DSN_AUDIT FAILED"
exit 1
