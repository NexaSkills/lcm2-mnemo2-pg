"""X-4 fail-close shim: psycopg (v3) missing in THIS interpreter.

Signature this cures (trade, debbie rebattery-close REPO-WATCH): plugin
`__file__` resolves into the repo and the host logs "Failed to load
plugin 'mnemo-pg2': load timed out after 10s (import + register() never
returned)" x216/day while the plugin silently 0-rows. Mechanism: the
plugin's load runs on a worker thread
(hermes_cli/plugins_loader._load_with_timeout); a top-level ImportError
raised there is captured and re-raised on the loader thread, but the
import-lane shadowing dance in these packages makes that path
non-deterministic — the observed failure mode is the worker *stuck*, so
the loud exception never surfaces. A missing dependency must fail FAST
and NAME IT on every route instead of racing a 10s timeout.

X-4B (TRADEFIX2-2305, the mybuddy-trade -default lane): the gateway
boot class where psycopg EXISTS on the box but is invisible to the
gateway interpreter — the launcher 'python -I' pops PYTHONPATH and
hermes_bootstrap re-selects an environment whose site-packages carries
no psycopg, while the deployment's psycopg-carrying environment (unit
drop-in / profile .env) is configured but never armed. require_psycopg
then fail-closes DETERMINISTICALLY (find_spec None, ModuleNotFoundError
name='psycopg') => 0 engine instance => 'no engine instance found' +
'falling back to built-in compressor' every turn. Before fail-closing,
every CONFIGURED site dir is adopted and re-probed.

This module ships in BOTH plugin dirs (mnemo-pg2 and lcm2-pg) and is
imported as the FIRST executable line of each package's pgconn.py,
before `import psycopg`. It does NOT create a psycopg stand-in: it
raises ImportError whose message names the missing dependency, the
interpreter, the install command, and the two plugin names — the fail-
close contract (R3: no silent fallback, ever).

Token MIKE-X10-0432 / W3TRADEFIX2-MIKE-2305
"""
from __future__ import annotations

import os
import sys
from importlib.util import find_spec

# Probed-path audit trails for the fail-close message (X-4B): dirs where
# the rescue FOUND psycopg vs dirs probed without success.
_RESCUED: list = []
_PROBED_NOTFOUND: list = []

_MISSING = (
    "lcm2-pg/mnemo-pg2 require psycopg (v3, psycopg[binary] or "
    "psycopg[c]) in the interpreter that loads them. This interpreter "
    "has none: {exe}. Install it into THAT interpreter's environment, "
    "e.g.:  {exe} -m pip install 'psycopg[binary]' psycopg-pool. "
    "NOTE: the hermes launcher wrapper runs 'python -I' (isolated mode) "
    "and system python3 are separate environments from any venv — "
    "install where the gateway/CLI process actually runs (check the "
    "failing process, not `which python3`). A missing psycopg here must "
    "fail loud at import, NOT via the plugin loader's 10s "
    "'load timed out' lane (the trade X-4 class: 216 silent timeouts, "
    "0 PG rows). Site dirs probed before failing: {probed}."
)


def _extra_site_dirs() -> list:
    """Site dirs the PROCESS was configured with but whose finder was
    never armed: LCM2_SITE_DIRS (colon list), LCM2_PG_SITE_DIR, the
    ambient PYTHONPATH, and the same keys in every loadable profile
    .env (fleet rule: profile .env is the one operator-sanctioned
    editable file). The launcher 'python -I' pops PYTHONPATH and
    hermes_bootstrap re-selects its own environment, so a deployment's
    psycopg-carrying site dir (unit drop-in / profile .env) can be
    configured yet invisible to this interpreter's finders
    (TRADEFIX2-2305: find_spec('psycopg') is None DETERMINISTICALLY at
    gateway boot on mybuddy-trade; the env-depcheck dual-surface rule
    names the other environment as the real one)."""
    out = []
    raw = (os.environ.get("LCM2_SITE_DIRS", "") + os.pathsep
           + os.environ.get("LCM2_PG_SITE_DIR", "") + os.pathsep
           + os.environ.get("PYTHONPATH", ""))
    homes = [os.environ.get("HERMES_HOME") or os.path.expanduser("~/.hermes")]
    prof_root = os.path.join(homes[0], "profiles")
    try:
        homes += [os.path.join(prof_root, p)
                  for p in sorted(os.listdir(prof_root))]
    except OSError:
        pass
    for h in homes:
        try:
            with open(os.path.join(h, ".env"), "r",
                      encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    line = line.strip()
                    if line.startswith(("LCM2_SITE_DIRS=", "LCM2_PG_SITE_DIR=",
                                        "PYTHONPATH=")):
                        raw += os.pathsep + line.split("=", 1)[1].strip()
        except OSError:
            continue
    # Root .env too (the DSN lives there on the fleet). Read-only,
    # site-dir keys only — no secrets touched.
    try:
        root_env = (os.environ.get("HERMES_ROOT_HOME")
                    or os.path.expanduser("~/.hermes"))
        with open(os.path.join(root_env, ".env"), "r",
                  encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if line.startswith(("LCM2_SITE_DIRS=", "LCM2_PG_SITE_DIR=",
                                    "PYTHONPATH=")):
                    raw += os.pathsep + line.split("=", 1)[1].strip()
    except OSError:
        pass
    for d in raw.split(os.pathsep):
        d = d.strip().strip('"').strip("'")
        if d and os.path.isdir(d) and d not in out:
            out.append(d)
    return out


def _psycopg_ok() -> bool:
    """True once psycopg is importable (bound or findable). Wrapped: a
    confused finder is a miss, not an error."""
    if "psycopg" in sys.modules:
        return True
    try:
        return find_spec("psycopg") is not None
    except Exception:
        return False


def _probe_path(path: str) -> bool:
    """Import psycopg with `path` made importable for the duration of
    the probe. Every meta_path finder is offered the dir explicitly
    (find_spec's `path` argument) — the honest form of "try this
    directory" that works no matter WHICH finder is healthy: the
    bootstrap class has no PathFinder at all, a normal interpreter
    answers via PathFinder, an editable-hook finder answers via its
    mapping. On failure a half-initialised psycopg husk left by the
    attempt is dropped so the NEXT candidate gets a clean shot (the
    'partially initialized module' corpse would poison later probes).
    On success the dir stays importable: psycopg is bound under its
    canonical key, so every later `import psycopg(.sub)` through the
    normal machinery finds it (pgconn's own top-level imports)."""
    if find_spec("psycopg") is not None:
        import psycopg  # noqa: F401
        return True
    served = False
    for finder in list(sys.meta_path):
        try:
            sp = finder.find_spec("psycopg", [path], None)
        except Exception:
            sp = None
        if sp is not None:
            served = True
            break
    if not served:
        # no finder serves the dir: last honest shot is a plain path
        # append (a healthy PathFinder answers a normal import then)
        added = path not in sys.path
        if added:
            sys.path.append(path)
        try:
            import psycopg  # noqa: F401
            return True
        except Exception:
            pass
        finally:
            if added and "psycopg" not in sys.modules:
                try:
                    sys.path.remove(path)
                except ValueError:
                    pass
        return False
    # a finder served the spec: import it and keep the binding on
    # success; drop the husk on failure so the next probe is clean.
    try:
        import importlib as _il
        _il.import_module("psycopg")
        return True
    except Exception:
        for k in [k for k in list(sys.modules)
                  if k == "psycopg" or k.startswith("psycopg.")]:
            try:
                f = getattr(sys.modules[k], "__file__", "") or ""
            except Exception:
                f = ""
            if f.startswith(path):  # this attempt's husk only
                sys.modules.pop(k, None)
        return False


def _rescue_site_probe() -> bool:
    """Adopt configured-but-unarmed site dirs and re-probe. Returns
    True once psycopg resolves. Bounded: at most one probe per
    candidate dir, reached ONLY after the primary finders fail. Two
    mechanisms only, in order of preference, both interpreter-
    sanctioned: site.addsitedir (arms the dir incl. its .pth finders),
    then the temporary-sys.path probe. NO private-key by-file exec is
    ever attempted — a package's relative-import semantics key off
    canonical sys.modules bindings and a private exec strands them
    (verified: relative imports inside a private-key exec resolve
    against the private name and die). Fail-open: False keeps the X-4
    fail-close in charge; it never fabricates a module."""
    if _psycopg_ok():
        return True
    import site as _site
    probed = []
    for dirs in (_extra_site_dirs(), list(sys.path)):
        for d in dirs:
            if not d or d in probed or not os.path.isdir(d):
                continue
            probed.append(d)
            try:
                _site.addsitedir(d)
                if _psycopg_ok():
                    _RESCUED.append(d)
                    return True
            except Exception:
                pass
            if _probe_path(d):
                _RESCUED.append(d)
                return True
    _PROBED_NOTFOUND.extend(probed)
    return False


def require_psycopg() -> None:
    """Raise a NAMED ImportError when psycopg is unimportable here.

    Checks sys.modules first (a live binding means the dependency is
    satisfied), then a cheap finders-only probe (no import side
    effects, no __import__ machinery running package code). X-4B
    (TRADEFIX2-2305): when the primary finders fail, every configured
    site dir is adopted and re-probed BEFORE the real attempt.
    Raises — never returns a stub."""
    if "psycopg" in sys.modules:
        return
    try:
        if find_spec("psycopg") is not None:
            return
    except (ImportError, ValueError, ModuleNotFoundError):
        pass  # finders confused: fall through to the real attempt below
    _rescue_site_probe()  # X-4B: arms a configured-but-unarmed dir; if
    # nothing resolved, the real attempt below names itself
    try:
        import psycopg  # noqa: F401  (real attempt; success = satisfied)
        return
    except ImportError as exc:
        if exc.name is not None and exc.name != "psycopg" \
                and not exc.name.startswith("psycopg."):
            return  # psycopg IS present; something else failed deeper —
                    # pgconn's own import surfaces that with its own name
        raise ImportError(
            _MISSING.format(exe=getattr(sys, "executable", "?"),
                            probed="; ".join(_RESCUED + _PROBED_NOTFOUND)
                            or "none (no candidate site dirs configured)")
        ) from exc
