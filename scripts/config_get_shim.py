#!/usr/bin/env python3
"""config_get_shim.py — route-2 visibility shim for `hermes config get <key>`.
#
# THE CLASS IT CURES (C2RESID2S R2, beez g1loader proof 2026-11-04): the
# gateway's tools-exe is a uv-made standalone CPython whose own
# site-packages carries psycopg but NEITHER ruamel NOR pyyaml. The hermes
# launcher runs it as `exe -I`: -I IGNORES PYTHONPATH *and* the user site
# (~/.local), so every systemd drop-in / --user install of the yaml stack
# is INVISIBLE to it. hermes_cli.config imports hermes_yaml (ruamel) at
# MODULE scope, so any config read made inside such an interpreter dies
# `ModuleNotFoundError: No module named 'ruamel'` and the key reads as
# UNSET — while `hermes config set` from a venv where ruamel exists
# writes the same key fine. Silent config split-brain.
#
# STRATEGY — passthrough-first, text-answer-last (stdlib only, NEVER
# imports hermes code or any yaml module):
#   1. exec the REAL hermes CLI. If it succeeds with non-empty stdout,
#      its answer wins verbatim (stdout AND rc passed through untouched).
#      On a host with a healthy env this shim is byte-invisible.
#   2. only if the CLI FAILED (non-zero rc — the ruamel class) or printed
#      nothing: answer the KEY ITSELF from config.yaml with a stdlib
#      regex scan (same semantics as engine_slot._config_engine_text:
#      top-level `context:` block, flow form, comments/quotes tolerated).
#      This is why the shim is key-shaped and NOT a mirror: nothing is
#      cached, nothing can go stale — every call re-reads the live files.
#      Precedence mirrors hermes: HERMES_HOME first, then $HOME/.hermes
#      (skipped under HOME_ISOLATION).
#   3. neither the CLI nor the files answer: propagate the CLI's rc.
#
# SCOPE LOCK: handles ONLY `hermes [-p X] config get <key>`. Anything
# else (a set/unset reaching here is a shim misconfiguration) is
# exec-passed through to the real CLI untouched. Read-only by design.
#
# Usage: config_get_shim.py <args...>   (argv shaped like the hermes CLI)
"""
import os
import re
import shutil
import subprocess
import sys

_GETTABLE_TOP = {"context"}  # dotted keys allowed under these prefixes


def _real_cli():
    which = shutil.which("hermes")
    if which:
        return [which]
    home = os.environ.get("HERMES_HOME") or os.path.expanduser("~/.hermes")
    for p in (home + "/hermes-agent/.hermes/bin/hermes",
              os.path.expanduser("~/.hermes/bin/hermes")):
        if os.access(p, os.X_OK):
            return [p]
    return None


def _scan(path, parts):
    """Text read of a dotted key path from a YAML file (stdlib regex,
    indentation-tracked block descent). Returns None when not found or
    unreadable."""
    if not path or not os.path.isfile(path):
        return None
    value = None
    stack = []  # (indent, is_dict) for the descent
    want = list(parts)
    depth = 0
    try:
        fh = open(path, encoding="utf-8", errors="replace")
    except OSError:
        return None
    with fh:
        for line in fh:
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            indent = len(line) - len(line.lstrip())
            s = line.strip()
            # pop stack to the level this line lives at
            while stack and indent <= stack[-1][0]:
                stack.pop()
            if len(stack) != depth:
                continue  # not on the wanted path
            key, _, val = s.partition(":")
            key = key.strip().strip("'\"")
            if key != want[depth]:
                continue
            val = val.split("#", 1)[0].strip()
            if depth + 1 == len(want):
                if val.startswith("{"):  # flow form: {k: v, ...}
                    m = re.search(r"\b%s\s*:\s*([^,}\s]+)" % re.escape(want[-1]), val)
                    return m.group(1).strip("'\"") if m else None
                if val == "":
                    value = "DICT"      # descend into children
                    stack.append((indent, True))
                    depth += 1
                    continue
                if val == "|":
                    return None         # multi-line block scalar: out of scope
                return val.strip("'\"")
            else:
                if val != "":
                    return None         # scalar where a dict is needed
                stack.append((indent, True))
                depth += 1
    return None


def _lookup(key):
    parts = [p for p in key.split(".") if p]
    if not parts or parts[0] not in _GETTABLE_TOP:
        return None
    home = os.environ.get("HERMES_HOME")
    files = [home + "/config.yaml"] if home else []
    if not os.environ.get("HOME_ISOLATION"):
        files.append(os.path.expanduser("~/.hermes/config.yaml"))
    for f in files:
        v = _scan(f, parts)
        if v is not None and v != "DICT":
            return v
    return None


def _parse(argv):
    argv = list(argv)
    profile = None
    if len(argv) >= 2 and argv[0] == "-p":
        profile = argv[1]
        argv = argv[2:]
    is_get = (len(argv) >= 3 and argv[0] == "config" and argv[1] == "get")
    return profile, is_get, (argv[2] if is_get else "")


def main(argv):
    profile, is_get, key = _parse(argv)
    cli = _real_cli()
    rc = None
    if cli is not None:
        # scope lock: everything but a handled config-get is exec passthrough
        if not (is_get and _lookup_ok(key)):
            try:
                os.execv(cli[0], cli + list(argv))
            except OSError:
                return subprocess.run(cli + list(argv)).returncode
        # handled config-get: real CLI first (a healthy env always wins).
        # NEVER mutate HERMES_HOME in this process's own environ: the
        # shim runs INSIDE gateway-side code; os.environ changes leak
        # into the host. Build the child env copy locally instead.
        env = dict(os.environ)
        if profile:
            base = env.get("HERMES_HOME") or os.path.expanduser("~/.hermes")
            env["HERMES_HOME"] = os.path.join(base.rstrip("/"), "profiles", profile)
        try:
            r = subprocess.run(cli + list(argv), capture_output=True,
                               text=True, timeout=90, env=env)
            rc = r.returncode
            if r.returncode == 0 and r.stdout.strip():
                sys.stdout.write(r.stdout)
                sys.stderr.write(r.stderr)
                return 0
        except (OSError, subprocess.SubprocessError) as exc:
            print("CONFIG_SHIM cli spawn failed: %s" % exc, file=sys.stderr)
    v = _lookup(key) if is_get else None
    if v is not None:
        print(v)
        print("CONFIG_SHIM served '%s' from config.yaml text scan "
              "(real CLI rc=%s — the ruamel/yaml class; see route2_note.sh)"
              % (key, rc), file=sys.stderr)
        return 0
    print("CONFIG_SHIM no answer: cli rc=%s, config.yaml text scan has no %r"
          % (rc, key), file=sys.stderr)
    return rc if rc is not None else 127


def _lookup_ok(key):
    parts = [p for p in (key or "").split(".") if p]
    return bool(parts) and parts[0] in _GETTABLE_TOP


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
