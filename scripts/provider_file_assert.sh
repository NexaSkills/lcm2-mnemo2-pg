#!/usr/bin/env bash
# provider_file_assert.sh — D-2 check-time assertion (repo-owned, stdlib-only).
#
# The memory provider that ACTUALLY serves the name 'mnemosyne' must be
# THIS REPO's mnemo-pg2 bridge/loader. Two independent ways that fails:
#   (a) a vendor/legacy mnemosyne plugin dir that is ENABLED (filesystem
#       route — the DBPROOF legacy-sqlite class), or
#   (b) no mnemo-pg2 in the enabled list at all (provider falls to a pip
#       entry-point or dies).
# Loader rule (verified 2026-11-09 fleet audit): ONLY plugins named in
# plugins.enabled load — a stale dir on disk is INERT while disabled. The
# original filesystem-order model false-RED'd correctly-configured hosts
# (all four fleet hosts carry an inert vendor dir; live writes provably
# go through the repo path).
#
# Exit 0 = repo provider serves 'mnemosyne'; 1 = bypass. FAILS CLOSED:
# if the enabled list cannot be read, the old filesystem rule still applies.
# Honors HERMES_BASE (profile base) like the other repo scripts; read-only.
set -uo pipefail
REPO=${REPO:-$(cd "$(dirname "$0")/.." && pwd)}
HERMES_BASE=${HERMES_BASE:-${HERMES_REAL_HOME:-$HOME}/.hermes}
SHIM="$(dirname "$0")/config_get_shim.py"

out=$(python3 - "$REPO" "$HERMES_BASE" "$SHIM" <<'PY'
import os, subprocess, sys
repo, base = os.path.realpath(sys.argv[1]), os.path.realpath(sys.argv[2])
shim = sys.argv[3]
roots = [os.path.join(base, "plugins")]
profs = os.path.join(base, "profiles")
if os.path.isdir(profs):
    for p in sorted(os.listdir(profs)):
        roots.append(os.path.join(profs, p, "plugins"))
hit = None
for r in roots:
    c = os.path.join(r, "mnemosyne", "__init__.py")
    if os.path.isfile(c):
        hit = os.path.realpath(c)
        break
# Loader truth: an enabled-list gate, not filesystem order. Scan THIS
# base's own config.yaml (stdlib text parse, same semantics as
# config_get_shim's fallback — passthrough CLI would ignore HERMES_BASE).
def _enabled_lists(base):
    # returns (found_config, mnemo_pg2_enabled) across base + all profiles
    import re
    cfgs = [os.path.join(base, "config.yaml")]
    cfgs += sorted(__import__("glob").glob(os.path.join(base, "profiles", "*", "config.yaml")))
    seen = False
    for f in cfgs:
        try:
            txt = open(f).read()
        except OSError:
            continue
        seen = True
        # enabled: block-list OR flow-list under a plugins: section
        m = re.search(r"^\s{2,}enabled:\s*\n((?:\s+-\s+\S+\n?)+)", txt, re.M)
        items = re.findall(r"-\s+(\S+)", m.group(1)) if m else []
        if not items:
            m2 = re.search(r"enabled:\s*\[([^\]]*)\]", txt)
            items = [x.strip() for x in (m2.group(1).split(","))] if m2 else []
        if "mnemo-pg2" in items:
            return True, True
    return seen, False
found, enabled = _enabled_lists(base)
print("file:", hit or "NO-DIR (entry-point route)")
print("repo:", repo)
print("mnemo-pg2 enabled:", enabled if found else "unknown (fail-closed)")
if hit and hit.startswith(repo + os.sep):
    ok = True  # provider file literally is ours
elif not found:
    ok = False  # fail-closed: no readable config to prove the gate
    print("note: no config.yaml found under base — cannot prove gate")
elif enabled:
    ok = True  # stale dirs are inert while disabled (loader rule)
    print("note: legacy mnemosyne dir present but DISABLED — inert")
else:
    ok = False
    print("note: mnemo-pg2 NOT enabled — 'mnemosyne' is served elsewhere")
print("ASSERT", "PASS" if ok else "FAIL")
sys.exit(0 if ok else 1)
PY
)
rc=$?
echo "$out"
exit $rc
