#!/usr/bin/env bash
# route2_note.sh — route-2 visibility note (C2RESID2S, READ-ONLY).
#
# Prints the CURRENT route-2 (config-driven engine selection) surface:
#   (a) whether the gateway exe can answer config reads AT ALL (the
#       ruamel class: hermes_cli.config imports hermes_yaml ->
#       ruamel.yaml at MODULE scope — a missing stack makes every
#       in-gateway config read answer UNSET while writes via other
#       interpreters succeed: the silent config split-brain), and
#   (b) the SAME key read TEXT-ANALOG-STYLE (regex on config.yaml, no
#       yaml module needed — the engine_slot._config_engine_text trick)
#       for root + every profile, so the note still tells the truth
#       about what the config NAMES when leg (a) is RED.
# When (a) is RED the note names the ONE config-set command form MANNY
# runs after a deploy (fleet rule: config edits are never Mike's leg;
# `hermes config set` is the only durable write form).
#
# Usage: route2_note.sh [engine-name]   (default lcm2-pg)
# Exit 0 always (a NOTE, not a gate); lines are grep-stable.
set -uo pipefail
NAME=${1:-lcm2-pg}
HB=${HERMES_BASE:-${HERMES_REAL_HOME:-$HOME}/.hermes}

# (a) gateway-interpreter config-read health
EXE=""
PID=$(sed -n 's/.*"pid"[^0-9]*\([0-9][0-9]*\).*/\1/p' "$HB/gateway.pid" 2>/dev/null | head -1)
[ -n "${PID:-}" ] && [ -r "/proc/$PID/cmdline" ] && { readarray -t -d '' A < "/proc/$PID/cmdline" 2>/dev/null || true; EXE=${A[0]:-}; }
[ -x "${EXE:-}" ] || EXE=$(command -v python3)
YOUT=$("$EXE" -I -c 'import importlib.util as u
def miss(m):
    try: return u.find_spec(m) is None
    except (ImportError, ValueError): return True
print("OK" if not any(miss(m) for m in ("ruamel.yaml","yaml")) else "MISSING")' 2>/dev/null)
if [ "$YOUT" = OK ]; then
  echo "ROUTE2 ok    gateway exe $EXE answers config reads (yaml stack importable, -I semantics)"
else
  echo "ROUTE2 RED   gateway exe $EXE CANNOT import the yaml stack: hermes_cli.config -> hermes_yaml -> ruamel.yaml dies at MODULE scope, so EVERY in-gateway 'hermes config get' answers UNSET while writes from other interpreters succeed (silent config split-brain). Operator legs (never Mike's):"
  echo "ROUTE2 FIX   install the stack where the exe's OWN -I sys.path reaches (the CLI launcher itself runs exe -I): $EXE -m pip install --target <exe-lib>/lib/python3.*/site-packages ruamel.yaml pyyaml   (a --user install is INVISIBLE under -I; verify after: $EXE -I -c 'import ruamel.yaml'); bounce from OUTSIDE the gateway"
  echo "ROUTE2 NOTE  config-set form MANNY runs per surface (the ONLY durable write leg; NEVER file-edit config.yaml):"
  echo "  hermes config set context.engine $NAME                    # root/launch scope"
  echo "  hermes -p <profile> config set context.engine $NAME       # per routed profile"
fi

# (b) what each config ACTUALLY NAMES — text fallback read, no yaml
# module, same scan semantics as engine_slot._config_engine_text
text_engine() {  # $1 = config.yaml path
  [ -f "$1" ] || { echo ""; return; }
  "$EXE" -I - "$1" <<'PY' 2>/dev/null
import re, sys
in_ctx = False
for line in open(sys.argv[1], encoding="utf-8", errors="replace"):
    if not line.strip() or line.lstrip().startswith("#"):
        continue
    indent = len(line) - len(line.lstrip())
    s = line.strip()
    if indent == 0:
        key, _, val = s.partition(":")
        if key.strip() != "context":
            in_ctx = False
            continue
        val = val.split("#", 1)[0].strip()
        if val.startswith("{"):
            m = re.search(r"engine\s*:\s*([^,}\s]+)", val)
            print(m.group(1).strip("'\"").lower() if m else "", end="")
            sys.exit(0)
        in_ctx = True
    elif in_ctx:
        m = re.match(r"engine\s*[,:]?\s*[:=]?\s*([^\s#]+)", s)
        if m:
            print(m.group(1).strip("'\"").lower(), end="")
            sys.exit(0)
PY
}
root_t=$(text_engine "$HB/config.yaml")
cli_root=$(hermes config get context.engine 2>/dev/null | tr -d '[:space:]')
if [ "$root_t" = "$NAME" ]; then echo "ROUTE2 ok    root context.engine=$root_t"; else echo "ROUTE2 note  root context.engine=${root_t:-<empty>} (want $NAME)"; fi
[ -n "$cli_root" ] && [ "$cli_root" != "$root_t" ] && echo "ROUTE2 note  root: CLI answers '$cli_root' but the FILE says '$root_t' (split-brain proof; leg (a) explains it)"
for d in "$HB"/profiles/*/; do
  [ -f "$d/config.yaml" ] || continue
  p=$(basename "$d")
  t=$(text_engine "$d/config.yaml")
  if [ "$t" = "$NAME" ]; then echo "ROUTE2 ok    $p context.engine=$t"; else echo "ROUTE2 note  $p context.engine=${t:-<empty>} (want $NAME)"; fi
done
exit 0
