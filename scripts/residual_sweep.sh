#!/usr/bin/env bash
# X-9 cure — residual marker sweep reliability. Census evidence
# (lcm2-fleet-sprint.result 03:0x + qa2 F-3): a probe row whose CONTENT
# carries brackets (e.g. "[CHECK] LCM2F-CHK32-...") survived the sweep —
# the DELETE matched 0 rows while the row was verifiably present. The
# failure class: a sweep whose DELETE relies on LIKE-pattern semantics
# (any engine flavoured by []/wildcard handling) silently matching NOTHING.
# This script's PRIMARY delete is position(marker IN content) — zero
# metacharacters by construction (immune to brackets, %, _), scoped to
# agent schemas ONLY (never public), printing per-schema before/deleted/
# after counts, and it NEVER touches a row that doesn't contain the marker.
#
# Usage: residual_sweep.sh --marker MARK [--schema S ...] [--dsn DSN]
#   no --schema  => discover mnemo2_*/lcm2_*/shared schemas in the db
#   default DSN  => the :54331 socket instance (mnemo2db)
# Exit 0 = every target schema marker-free afterwards; 1 = residue left.
set -uo pipefail
PORT=${PORT:-54331}
BASE=${BASE:-$HOME/lcm2-mnemo2-pg/pg}
DSN=""
MARKER=""
SCHEMAS=()
while [ $# -gt 0 ]; do
  case "$1" in
    --dsn)     DSN="$2"; shift 2 ;;
    --marker)  MARKER="$2"; shift 2 ;;
    --schema)  SCHEMAS+=("$2"); shift 2 ;;
    *) echo "residual_sweep: unknown arg '$1'" >&2; exit 2 ;;
  esac
done
[ -n "$MARKER" ] || { echo "residual_sweep: --marker required" >&2; exit 2; }
DSN=${DSN:-"dbname=mnemo2db host=$BASE/run port=$PORT user=mnemo201"}
if [ ${#SCHEMAS[@]} -eq 0 ]; then
  # discover every agent schema in the db (lcm2_* / mnemo2_* / shared)
  dsn_db=$(printf '%s' "$DSN" | sed -n 's/.*dbname=\([^ ]*\).*/\1/p')
  dsn_rest=$(printf '%s' "$DSN" | sed 's/dbname=[^ ]* *//;s/ *$//')
  psql_bin="psql"; [ -x "$BASE/bin/psql" ] && psql_bin="$BASE/bin/psql"
  mapfile -t SCHEMAS < <("$psql_bin" ${dsn_rest:+"$dsn_rest"} -d "${dsn_db:-mnemo2db}" -Atqc \
    "select nspname from pg_namespace where nspname like 'mnemo2\_%' or nspname like 'lcm2\_%' or nspname='shared'" 2>/dev/null)
fi
[ ${#SCHEMAS[@]} -gt 0 ] || { echo "residual_sweep RED $(hostname) (no target schemas)" >&2; exit 1; }

PY=$(command -v python3)
"$PY" - "$DSN" "$MARKER" "${SCHEMAS[@]}" <<'PYEOF'
import sys
dsn, marker, schemas = sys.argv[1], sys.argv[2], sys.argv[3:]
try:
    import psycopg
except ImportError:  # repo fail-close convention (X-4 family)
    sys.exit("residual_sweep: psycopg missing for " + sys.executable +
             " — install: pip install 'psycopg[binary]'")
rc = 0
for s in schemas:
    if not (s.startswith("mnemo2_") or s.startswith("lcm2_") or s == "shared"):
        print(f"residual_sweep SKIP {s} (not an agent schema)")
        continue
    with psycopg.connect(dsn, application_name="residual-sweep") as cx:
        cnt = (f'select count(*) from "{s}".working_memory '
               "where position(%(m)s in content) > 0")
        before = cx.execute(cnt, {"m": marker}).fetchone()[0]
        n = cx.execute(
            f'delete from "{s}".working_memory '
            "where position(%(m)s in content) > 0", {"m": marker}).rowcount
        after = cx.execute(cnt, {"m": marker}).fetchone()[0]
        status = "ok" if after == 0 else "RESIDUE"
        if status != "ok":
            rc = 1
        print(f"residual_sweep {s} before={before} deleted={n} after={after} {status}")
sys.exit(rc)
PYEOF
rc=$?
if [ $rc -eq 0 ]; then
  echo "SWEEP_GREEN $(hostname) $(date -Iseconds) marker=$MARKER schemas=${#SCHEMAS[@]}"
else
  echo "SWEEP_RED $(hostname) $(date -Iseconds) marker=$MARKER"
fi
exit $rc
