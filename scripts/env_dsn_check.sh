#!/usr/bin/env bash
# env_dsn_check.sh — deterministic assertion: every LCM2_PG_DSN / MNEMO2_PG_DSN
# line in the given .env files (default: root + all profile .env) parses as ONE
# clean URI. Concatenated/merged DSN values (a sed -i whose replacement carried
# a trailing newline merges consecutive KEY= lines) parse as
#   'extra key/value separator "=" in URI query parameter: "port"'
# and the plugin's fail-closed (R3, zero-fallback) path then lands every write
# into the built-in sqlite engine — a LISTENing :54331 beside a pure-sqlite
# runtime, invisible to role/db/schema checks. Same never-trust-the-surface
# family as bootstrap_verify A1 and the T-2 lesson.
# Exit non-zero on any malformed DSN line; print the offending file:line.
# Usage: env_dsn_check.sh [file ...]
set -uo pipefail
FILES=("$@")
if [ ${#FILES[@]} -eq 0 ]; then
  HB=${HERMES_BASE:-${HERMES_REAL_HOME:-$HOME}/.hermes}
  FILES=("$HB/.env")
  for f in "$HB"/profiles/*/.env; do [ -f "$f" ] && FILES+=("$f"); done
fi
FAIL=0
for f in "${FILES[@]}"; do
  [ -f "$f" ] || continue
  while IFS=: read -r ln line; do
    val=${line#*=}
    case "$val" in
      *$'\n'*) : ;;  # can't happen from a single line
    esac
    # malformed if the VALUE contains another DSN key occurrence (= concatenation)
    case "$val" in
      *LCM2_PG_DSN=*|*MNEMO2_PG_DSN=*)
        echo "ENVDSN_FAIL: $f:$ln merged DSN value" >&2; FAIL=1 ;;
    esac
    # malformed URI check via the real parser (libpq), if available
    if command -v psql >/dev/null 2>&1; then
      err=$(psql "$val" -Atc 'select 1' 2>&1 </dev/null | head -1)
      case "$err" in
        *"extra key/value separator"*|*invalid*URI*)
          echo "ENVDSN_FAIL: $f:$ln unparseable URI ($err)" >&2; FAIL=1 ;;
      esac
    fi
  done < <(grep -nE '^(LCM2_PG_DSN|MNEMO2_PG_DSN)=' "$f" 2>/dev/null | sed 's/:/=/' | sed 's/^\([0-9]*\)=\(.*\)/\1:\2/')
done
[ "$FAIL" = 0 ] && { echo "ENVDSN_CHECK OK (${#FILES[@]} files)"; exit 0; } || { echo "ENVDSN_CHECK FAILED"; exit 1; }
