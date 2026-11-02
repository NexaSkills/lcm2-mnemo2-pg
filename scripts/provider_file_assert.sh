#!/usr/bin/env bash
# provider_file_assert.sh — D-2 check-time assertion (repo-owned, stdlib-only).
#
# The memory provider that ACTUALLY serves the name 'mnemosyne' must
# resolve INTO THIS REPO (the mnemo-pg2 bridge/loader). Anything else —
# a legacy real dir under $HERMES_HOME/plugins/mnemosyne, a pip
# entry-point package (mnemosyne-hermes in some venv site-packages), a
# stale module dir — means agent memory writes bypass the zero-fallback
# mnemo-pg2 path (the DBPROOF legacy-sqlite class).
#
# Exit 0 = provider __file__ under repo realpath; 1 = anything else.
# Honors HERMES_BASE (profile base) like the other repo scripts; read-only.
set -uo pipefail
REPO=${REPO:-$(cd "$(dirname "$0")/.." && pwd)}
HERMES_BASE=${HERMES_BASE:-${HERMES_REAL_HOME:-$HOME}/.hermes}

out=$(python3 - "$REPO" "$HERMES_BASE" <<'PY'
import os, sys
repo, base = os.path.realpath(sys.argv[1]), os.path.realpath(sys.argv[2])
# Resolution order mirrors the host loader (plugins/memory/__init__.py):
# bundled (none for mnemosyne) > user plugins > profile plugins (via
# profile dirs) > project plugins > pip entry point.
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
print("file:", hit or "NO-DIR (entry-point route)")
print("repo:", repo)
ok = bool(hit) and hit.startswith(repo + os.sep)
print("ASSERT", "PASS" if ok else "FAIL")
sys.exit(0 if ok else 1)
PY
)
rc=$?
echo "$out"
exit $rc
