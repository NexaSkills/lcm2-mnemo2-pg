"""g1loader_probe — read-only G1 loader probe (C2RESID2-MIKE-2310).

Answers route 1 of the host's context-engine loader:

    find_engine_dir(name) -> dir  AND  load_context_engine(name) -> non-None

for the CURRENT launch scope (route-1 resolution is launch-scope:
user_plugins_dir() = get_hermes_home()/plugins; HERMES_PROFILE is
IGNORED — gate-proved C2DEPLOY-GATE-TESSA-2236), and — when the
gateway multiplexes profiles (gateway.multiplex_profiles: true) — for
EVERY profile dir under <home>/profiles as a routed turn would see it.

Faithfulness rules baked in (the F3/R4 / C2DEPLOY-GATE class this probe
exists to catch):
  * the launcher's child pops PYTHONHOME/PYTHONPATH/VIRTUAL_ENV before
    any plugin import — so this probe POPS them too and NEVER
    re-injects. An env that only answers with PYTHONPATH present
    reports NO-INSTANCE here, exactly as it falls back on a routed turn;
  * the verdict is the LOADER CALL, never an import-health proxy
    (psycopg importable != engine lane alive);
  * route-1 path resolution is reported EXPLICITLY from the source
    helper (user_plugins_dir()), never inferred from an ambient var —
    an entry point that resolves through the process home while an
    env-set probe reports something else is the dead-lane signature;
  * the capture loader swallows register() failures at debug level:
    plugins.context_engine warnings are surfaced.

Usage:
    python3 -I g1loader_probe.py [engine-name] [--home <path>] [--quiet]

Output (grep-stable):
    G1SURFACE scope=root|profile:<name> home=<path> engine=<name> ...
    route1_plugins_dirs(<name>) = [dirs actually consulted]
    find_engine_dir(<name>) = <dir|None>
    load_context_engine(<name>) = <module.Class|None>
    G1LOADER INSTANCE | G1LOADER NO-INSTANCE

Read-only: imports only (the engine's own idempotent bootstrap DDL may
run — what a routed turn does anyway). Zero config/gateway/DB writes.
"""
import os
import re
import sys


def _checkout_root(home):
    """Locate the hermes-agent checkout whose plugins/ the loader runs."""
    cands = [os.environ.get("HERMES_ROOT"), os.environ.get("HERMES_AGENT_ROOT")]
    if home:
        if "/profiles/" in home:
            cands.append(home.split("/profiles/", 1)[0] + "/hermes-agent")
        else:
            cands.append(home.rstrip("/") + "/hermes-agent")
    cands.append(os.path.expanduser("~/.hermes/hermes-agent"))
    for c in cands:
        if c and os.path.isfile(os.path.join(c, "plugins", "context_engine",
                                             "__init__.py")):
            return os.path.abspath(c)
    return None


def _multiplexes():
    try:
        import hermes_cli.config as cfg
        conf = cfg.load_config_readonly({}) or {}
        gw = conf.get("gateway") or {}
        return bool(gw.get("multiplex_profiles") or gw.get("multiplex"))
    except Exception:
        return False


def _route1_dirs_report(ce_mod, name):
    """Print the dirs route 1 actually consults, resolved from source."""
    dirs = []
    try:
        src = __import__("inspect").getsource(ce_mod)
        m = re.search(r"def\s+find_engine_dir\b[\s\S]*?(?=\ndef |\Z)", src)
        if m and "user_plugins_dir" in m.group(0):
            try:
                from hermes_constants import get_hermes_home
                dirs.append(str(get_hermes_home() / "plugins" /
                                "context_engine" / name))
            except Exception:
                pass
        for extra in re.findall(r'REPO_ROOT\s*[/*]\s*["\']plugins/context_engine["\']',
                                m.group(0)) if m else []:
            pass
        try:
            import hermes_constants
            rr = getattr(hermes_constants, "get_repo_root", None)
            if rr and m and "REPO_ROOT" in m.group(0):
                dirs.append(os.path.join(rr(), "plugins", "context_engine", name))
        except Exception:
            pass
    except Exception:
        pass
    if not dirs:
        # resolution unknown: report the env candidate, clearly labelled
        home = os.environ.get("HERMES_HOME") or os.path.expanduser("~/.hermes")
        dirs.append("%s(via-env-candidate; source-resolution-unread)" %
                    os.path.join(home, "plugins", "context_engine", name))
    print("route1_plugins_dirs(%s) = %s" % (name, dirs), flush=True)


def _routed_profiles(home):
    out = []
    pdir = os.path.join(home, "profiles")
    try:
        for p in sorted(os.listdir(pdir)):
            d = os.path.join(pdir, p)
            if os.path.isfile(os.path.join(d, "config.yaml")):
                out.append((p, d))
    except OSError:
        pass
    return out


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    name = "lcm2-pg"
    quiet = "--quiet" in argv
    home = None
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--home" and i + 1 < len(argv):
            home = argv[i + 1].rstrip("/")
            i += 2
            continue
        if a == "--quiet":
            i += 1
            continue
        if "/" in a:
            home = a.rstrip("/")
        elif a and not a.startswith("-"):
            name = "".join(c for c in a if c.isalnum() or c in "-_.") or name
        i += 1

    # launcher-child-faithful env (see module docstring)
    for k in ("PYTHONHOME", "PYTHONPATH", "VIRTUAL_ENV"):
        os.environ.pop(k, None)
    if home:
        os.environ["HERMES_HOME"] = home
    home_eff = os.environ.get("HERMES_HOME") or os.path.expanduser("~/.hermes")

    root = _checkout_root(home_eff)
    print("G1SURFACE scope=root home=%s engine=%s exe=%s root=%s" %
          (home_eff, name, sys.executable, root), flush=True)
    if root is None:
        print("G1LOADER NO-INSTANCE (hermes-agent checkout with "
              "plugins/context_engine not found)", flush=True)
        return 2
    if root not in sys.path:
        sys.path.insert(0, root)

    import logging
    logging.basicConfig(level=logging.ERROR if quiet else logging.WARNING,
                        stream=sys.stderr, force=True)
    for noisy in ("asyncio", "urllib3", "urllib", "botocore"):
        logging.getLogger(noisy).setLevel(logging.ERROR)
    ce_log = logging.getLogger("plugins.context_engine")
    ce_log.setLevel(logging.DEBUG if quiet else logging.INFO)

    try:
        import plugins.context_engine as ce
    except BaseException as exc:
        print("probe RAISED (loader-unimportable): %s %s" %
              (type(exc).__name__, str(exc)[:200]), flush=True)
        print("G1LOADER NO-INSTANCE", flush=True)
        return 2

    rc = 0

    def run(label):
        nonlocal rc
        _route1_dirs_report(ce, name)
        try:
            d = ce.find_engine_dir(name)
            e = ce.load_context_engine(name)
        except BaseException as exc:
            print("find_engine_dir(%s) [%s] RAISED: %s %s" %
                  (name, label, type(exc).__name__, str(exc)[:160]), flush=True)
            print("load_context_engine(%s) [%s] = None" % (name, label), flush=True)
            rc = 2
            return
        eng = ("%s.%s" % (type(e).__module__, type(e).__name__)) if e is not None else None
        print("find_engine_dir(%s) [%s] = %s" % (name, label, d), flush=True)
        print("load_context_engine(%s) [%s] = %s" % (name, label, eng), flush=True)
        if d is None or e is None:
            rc = 2

    run("root")
    if _multiplexes():
        for pname, pdir in _routed_profiles(home_eff):
            print("G1SURFACE scope=profile:%s home=%s engine=%s routed=multiplex" %
                  (pname, pdir, name), flush=True)
            prev = os.environ.get("HERMES_HOME", "")
            os.environ["HERMES_HOME"] = pdir
            try:
                run("profile:%s" % pname)
            finally:
                os.environ["HERMES_HOME"] = prev

    print("G1LOADER %s" % ("INSTANCE" if rc == 0 else "NO-INSTANCE"), flush=True)
    return 0 if rc == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
