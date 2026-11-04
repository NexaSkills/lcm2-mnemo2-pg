#!/usr/bin/env bash
# env_yaml_probe.sh — fail-closed probe: can a DEFAULT-start python (no
# -I, no PYTHONPATH — the hermes CLI / subprocess reality, e.g.
# hermes_constants' config reader shelling out to the `hermes` CLI)
# import hermes_yaml's dependency chain (ruamel.yaml + yaml)?
# Root cause class (C2RESID2S): the gateway exe's site dirs carry
# psycopg but NOT ruamel/pyyaml, so `hermes config get` inside the
# gateway interpreter dies ModuleNotFoundError -> engine reads as unset.
# Read-only; changes nothing.
# Usage: env_yaml_probe.sh [exe]   (default: resolve the LIVE gateway exe
#   from the pidfile, like env_depcheck.sh; fallback python3)
# Exit 0 = yaml stack importable by default-start; 2 = NOT (names the
#   env + exact pip cure).
set -uo pipefail
HB=${HERMES_BASE:-${HERMES_REAL_HOME:-$HOME}/.hermes}
EXE=${1:-}
if [ -z "$EXE" ]; then
  PID=$(sed -n 's/.*"pid"[^0-9]*\([0-9][0-9]*\).*/\1/p' "$HB/gateway.pid" 2>/dev/null | head -1)
  [ -n "${PID:-}" ] && [ -r "/proc/$PID/cmdline" ] && readarray -t -d '' A < "/proc/$PID/cmdline" 2>/dev/null
  EXE=${A[0]:-}
fi
[ -n "${EXE:-}" ] && [ -x "$EXE" ] || EXE=$(command -v python3)
[ -x "$EXE" ] || { echo "YAMLPROBE RED no interpreter" >&2; exit 2; }
OUT=$(env -u PYTHONPATH -u PYTHONHOME -u VIRTUAL_ENV "$EXE" -c \
  'import importlib.util as u,sys
def miss(m):
    try: return u.find_spec(m) is None
    except (ImportError, ValueError): return True
missing=[m for m in ("ruamel.yaml","yaml") if miss(m)]
print("OK" if not missing else "MISSING:"+",".join(missing)); sys.exit(0 if not missing else 3)' 2>&1)
rc=$?
if [ "$rc" = 0 ] && [ "$OUT" = OK ]; then
  echo "YAMLPROBE ok    default-start $EXE imports ruamel.yaml + yaml"
  exit 0
fi
echo "YAMLPROBE RED  default-start $EXE CANNOT import: ${OUT:-probe failed rc=$rc}" >&2
echo "YAMLPROBE FIX  hermes_cli.config imports hermes_yaml at module scope (ruamel.yaml); a config read that shells out to the hermes CLI dies with ModuleNotFoundError in THIS env, so context.engine reads as unset everywhere downstream. Install the config stack into the site-packages dirs $EXE carries by DEFAULT (its lib/.../site-packages and ~/.local/lib/<ver>/site-packages — env_depcheck's PYTHONPATH probe says nothing about this surface):" >&2
echo "      $EXE -m pip install --user ruamel.yaml pyyaml" >&2
echo "  (or into $EXE's own site-packages: $EXE -m pip install --target <dir-on-its-default-sys.path> ruamel.yaml pyyaml; then bounce from OUTSIDE the gateway process)" >&2
exit 2
