#!/usr/bin/env bash
# g1loader_patch.sh — G1 ruamel cure helper (C2RESID2S; DESIGN DRAFT —
# NOT SHIPPED SURFACE; the EXEC leg is MANNY's, see CLOSE row
# C2RESID2S-MIKE-0253. Mike never touches gateway env or bounces).
#
# WHAT IT PROVES (run as-is, read-only): the exact reason g1loader emits
# "Failed to load context engine 'lcm2-pg': No module named 'ruamel'"
# even though the gateway's systemd PYTHONPATH carries a venv WITH
# ruamel (9527-venv): hermes_cli/config.py line ~34 does
# `import hermes_yaml as yaml` at MODULE scope and hermes_yaml.py imports
# ruamel.yaml, so ANY in-gateway import chain reaching hermes_cli.config
# (user_plugin_host -> _plugins_config -> load_config_readonly) dies
# when the interpreter actually running lacks ruamel — and the
# plugin-host / CLI child processes run `exe -I`, which STRIPS the
# drop-in PYTHONPATH entirely (g1loader_probe pops it faithfully).
#
# USAGE (manny, from a shell OUTSIDE the gateway):
#   bash g1loader_patch.sh            # prints the probe verdicts only
#   bash g1loader_patch.sh --install  # ALSO: uv pip install the yaml
#       # stack into the launcher env (the venv the runtime command
#       # names), so the -I children of THAT launcher import ruamel;
#       # a bounce from outside the gateway is still required after.
set -uo pipefail
HB=${HERMES_BASE:-${HERMES_REAL_HOME:-$HOME}/.hermes}
LAUNCHER=$(command -v hermes || true)
[ -x "${LAUNCHER:-}" ] || LAUNCHER="$HB/hermes-agent/.hermes/bin/hermes"

RJ=$(env -i HOME="$HOME" HERMES_HOME="$HB" "$LAUNCHER" --print-runtime-command gateway run 2>/dev/null | tail -1)
ENVBIN=""
case "${RJ:-}" in \[*)
  ENVBIN=$(python3 -c 'import json,sys; print(json.loads(sys.stdin.read())[0])' <<<"$RJ" 2>/dev/null) ;;
esac
echo "launcher runtime exe: ${ENVBIN:-<unresolved>}"
[ -x "${ENVBIN:-}" ] || { echo "PATCH cannot resolve the launcher runtime env — inspect manually"; exit 2; }
"$ENVBIN" -c 'import ruamel.yaml, yaml; print("launcher env: ruamel+yaml OK")' 2>/dev/null \
  || echo "launcher env: MISSING ruamel/yaml  <- the g1loader blocker lives here"

if [ "${1:-}" = "--install" ]; then
  VENVROOT=$(dirname "$(dirname "$ENVBIN")")
  (command -v uv >/dev/null && uv pip install --python "$ENVBIN" ruamel.yaml pyyaml) \
    || "$ENVBIN" -m pip install ruamel.yaml pyyaml \
    || { echo "PATCH install failed (operator leg)"; exit 2; }
  "$ENVBIN" -c 'import ruamel.yaml, yaml' && echo "PATCH installed — now bounce the gateway FROM OUTSIDE it (manny's leg) and re-run g1loader_verify.sh"
fi
exit 0
