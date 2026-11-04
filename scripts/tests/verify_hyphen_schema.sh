#!/usr/bin/env bash
# W3TKT-MIKE-1220 ticket #3 — hyphen-named profile schemas.
# A hyphenated profile id (demo-freshkey) is VALID for hermes
# (hermes_constants.PROFILE_ID_RE = ^[a-z0-9][a-z0-9_-]{0,63}$) but a bare
# `CREATE SCHEMA IF NOT EXISTS lcm2_demo-freshkey` is a SQL syntax error
# (parser splits at the minus), so onboard died at §2 under set -e.
# Cure: double-quoted identifiers everywhere scripts/ creates schemas.
#
# Harness shape (no real postgres needed, works on any host):
#  * a fake-psql shim records every SQL string the scripts emit AND answers
#    "syntax error at or near"- on any bare hyphenated identifier (mirrors
#    real PG grammar: quoted hyphen id OK, bare hyphen id RED — the exact
#    behaviour proven live against :54331 during this ticket);
#  * GREEN leg: onboard_profile.sh + setup_dbs.sh onboarding profile
#    "demo-freshkey" must emit QUOTED lcm2_/mnemo2_ identifiers;
#  * RED leg: the shim rejects a bare hyphen id (sanity: the test itself
#    would catch the pre-fix form).
# Exit 0 = PASS. This file FAILED against the pre-fix scripts (they emitted
# the bare identifier; the shim returned the syntax error and onboard died).
set -u
REPO="${REPO:-$(cd "$(dirname "$0")/../.." && pwd)}"
ROOT=$(mktemp -d /tmp/hyphschema.XXXXXX)
trap 'rm -rf "$ROOT"' EXIT
mkdir -p "$ROOT/bin" "$ROOT/sql"

cat > "$ROOT/bin/psql" <<'EOF'
#!/usr/bin/env bash
# fake psql: log SQL; emulate PG identifier grammar for CREATE SCHEMA.
sql=""
mode=0
for a in "$@"; do
  if [ "$mode" = 1 ]; then sql="$a"; mode=0; continue; fi
  # -c, or bundled flags ending in c (e.g. -qc, -Atc) carry the SQL string
  case "$a" in -c|-*c) mode=1 ;; esac
done
[ -n "$sql" ] || exit 0
printf '%s\n' "$sql" >> "${FAKE_PSQL_LOG:-/dev/null}"
# emulate PG: quoted identifier always OK; bare token with '-' = syntax error
case "$sql" in
  *CREATE\ SCHEMA\ IF\ NOT\ EXISTS\ *)
    rest=${sql#*CREATE SCHEMA IF NOT EXISTS }
    ident=${rest%%[ ;$'\n']*}
    case "$ident" in
      \"*\") exit 0 ;;
      *\"*|\"\") { echo "ERROR:  invalid identifier form" >&2; exit 1; } ;;
      *)
        stripped=${ident//[a-z0-9_]/}
        [ -z "$stripped" ] || { echo "ERROR:  syntax error at or near \"$stripped\"" >&2; exit 1; }
        exit 0 ;;
    esac ;;
esac
exit 0
EOF
chmod +x "$ROOT/bin/psql"

# minimal fake hermes (config get/set no-ops; plugins list "already enabled")
cat > "$ROOT/bin/hermes" <<'EOF'
#!/usr/bin/env bash
case "$*" in
  *"config get"*) echo "lcm2-pg" ;;
  *"plugins list"*) echo "│ lcm2-pg   │ enabled │"; echo "│ mnemo-pg2   │ enabled │" ;;
esac
exit 0
EOF
chmod +x "$ROOT/bin/hermes"
export PATH="$ROOT/bin:$PATH"

FAIL=0
run_leg() {  # run_leg <label> <script> <args...>
  local label=$1 script=$2; shift 2
  : > "$ROOT/sql/$label"
  FAKE_PSQL_LOG="$ROOT/sql/$label" \
  REPO="$REPO" BASE="$ROOT/pg" PORT=54331 ROLE=mnemo201 \
  PSQL="$ROOT/bin/psql" PSQL_BIN="$ROOT/bin/psql" \
  HERMES_BASE="$ROOT/.hermes" \
    bash "$script" "$@" > "$ROOT/sql/$label.out" 2>&1
  echo "== leg $label rc=$? =="
}

mkdir -p "$ROOT/.hermes/profiles" "$ROOT/pg/run"

# --- LEG 1: onboard_profile.sh with a hyphen-named profile ------------------
# Pre-seed config.yaml so §1 skips real create; the loader leg (§4+) uses
# the fake hermes. The SQL that MATTERS is §2's three CREATE SCHEMA lines.
mkdir -p "$ROOT/.hermes/profiles/demo-freshkey"
: > "$ROOT/.hermes/profiles/demo-freshkey/config.yaml"
run_leg onboard "$REPO/scripts/onboard_profile.sh" demo-freshkey
grep -q 'CREATE SCHEMA IF NOT EXISTS \\"lcm2_demo-freshkey\\"\|CREATE SCHEMA IF NOT EXISTS "lcm2_demo-freshkey"' "$ROOT/sql/onboard" \
  && echo "PASS onboard emits QUOTED lcm2_demo-freshkey" \
  || { echo "FAIL onboard did not quote lcm2_demo-freshkey"; sed -n 1,10p "$ROOT/sql/onboard"; FAIL=1; }
grep -q 'mnemo2_demo-freshkey' "$ROOT/sql/onboard" \
  && grep -q '\\"mnemo2_demo-freshkey\\"\|"mnemo2_demo-freshkey"' "$ROOT/sql/onboard" \
  && echo "PASS onboard emits QUOTED mnemo2_demo-freshkey" \
  || { echo "FAIL onboard mnemo2 schema missing/unquoted"; FAIL=1; }
grep -q 'syntax error' "$ROOT/sql/onboard.out" \
  && { echo "FAIL onboard leg hit the syntax-error path (pre-fix behaviour)"; FAIL=1; } \
  || echo "PASS onboard leg never hit syntax error"

# --- LEG 2: setup_dbs.sh (same quoting contract) ----------------------------
run_leg setupdbs "$REPO/scripts/setup_dbs.sh" demo-freshkey
grep -q 'syntax error' "$ROOT/sql/setupdbs.out" \
  && { echo "FAIL setup_dbs leg hit the syntax-error path (pre-fix behaviour)"; FAIL=1; } \
  || echo "PASS setup_dbs leg never hit syntax error"
grep -q 'CREATE SCHEMA IF NOT EXISTS \\"lcm2_demo-freshkey\\"\|CREATE SCHEMA IF NOT EXISTS "lcm2_demo-freshkey"' "$ROOT/sql/setupdbs" \
  && echo "PASS setup_dbs emits QUOTED lcm2_demo-freshkey" \
  || { echo "FAIL setup_dbs did not quote the hyphen schema"; FAIL=1; }

# --- LEG 3: RED-leg sanity — the shim rejects the PRE-FIX bare form --------
if FAKE_PSQL_LOG=/dev/null "$ROOT/bin/psql" -c 'CREATE SCHEMA IF NOT EXISTS lcm2_demo-freshkey' 2>/dev/null; then
  echo "FAIL harness shim accepted the bare hyphen identifier (test would not catch pre-fix)"; FAIL=1
else
  echo "PASS harness shim rejects bare hyphen identifier (pre-fix would be caught)"
fi
if FAKE_PSQL_LOG=/dev/null "$ROOT/bin/psql" -c 'CREATE SCHEMA IF NOT EXISTS "lcm2_demo-freshkey"' 2>/dev/null; then
  echo "PASS harness shim accepts quoted hyphen identifier"
else
  echo "FAIL harness shim rejected the quoted identifier"; FAIL=1
fi

[ "$FAIL" = 0 ] && echo "HYPHEN_SCHEMA_HARNESS PASS" || echo "HYPHEN_SCHEMA_HARNESS FAIL"
exit $FAIL
