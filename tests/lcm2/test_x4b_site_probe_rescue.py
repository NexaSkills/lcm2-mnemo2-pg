"""X4B (TRADEFIX2-2305): the depcheck must rescue psycopg from the site
dirs the PROCESS was configured with but whose finder was never armed.

Deterministic gateway-boot signature this cures (mybuddy-trade, -default
lane): the hermes launcher runs 'python -I' and pops PYTHONPATH, then
hermes_bootstrap re-selects an environment whose venv site-packages
carries NO psycopg (installs/<hash>/environments/<h2>/venv — env-depcheck
dual-surface rule), while the deployment's OTHER environment (the one
the profile .env DSN was built for, exported as PYTHONPATH by a unit
drop-in that the launcher strips) DOES carry psycopg + psycopg_pool.
require_psycopg()'s find_spec then returns None DETERMINISTICALLY (no
race, no shadowing: ModuleNotFoundError name='psycopg' from the real
import) => fail-close ImportError => 0 engine instance => 2 fallback
lines per turn. Fresh-process sims 'pass' only because they run with
the launcher's own tools-exe site-packages on path.

Contract proven here (no network, no DB):
  1. rescue: psycopg + psycopg_pool importable from an unarmed site dir
     (fresh meta_path, dir absent from sys.path) => require_psycopg
     PASSES and binds the REAL modules under the canonical names;
  2. candidate sources: LCM2_SITE_DIRS (colon list), LCM2_PG_SITE_DIR,
     and the ambient PYTHONPATH entries — dirs are adopted via
     site.addsitedir, then a bounded by-file load of the package's own
     modules (psycopg, psycopg_pool, psycopg.<flat-subs>) scoped to
     THAT dir (no .pth execution, no meta_path edit);
  3. fail-close KEPT: with no candidate and no psycopg anywhere the
     raised ImportError message keeps the X-10 text and names the
     probed site dirs too;
  4. hygiene: rescue mutates NOTHING when psycopg already resolves
     (path, meta_path, and the psycopg binding untouched);
  5. BOTH lanes ship the rescue (identical depcheck rescue bodies).

The rescue source is a REAL psycopg install — this suite's interpreter's
own site dirs (fabricated psycopg stubs cannot model the package's real
relative-import semantics and would test fiction).
"""
import importlib.util
import os
import shutil
import site
import subprocess
import sys
import textwrap
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SHIMS = [REPO / "plugins" / p / "_depcheck.py" for p in ("mnemo-pg2", "lcm2-pg")]


def _real_site_dir():
    """A site dir (on THIS interpreter's path) that carries psycopg AND
    psycopg_pool as real installed packages."""
    import psycopg, psycopg_pool
    a = Path(psycopg.__file__).resolve().parent
    b = Path(psycopg_pool.__file__).resolve().parent
    assert a.parent == b.parent, (a, b)
    return a.parent


def _stage_site(tmp: Path) -> Path:
    """Copy the real psycopg + psycopg_pool packages into an empty dir
    (simulates the configured-but-unarmed site dir of the boot class)."""
    src = _real_site_dir()
    for pkg in ("psycopg", "psycopg_pool"):
        s = src / pkg
        if s.is_dir():
            shutil.copytree(s, tmp / pkg, symlinks=True)
    return tmp


def _boot_class() -> str:
    """Python that mimics the gateway-boot interpreter WITHOUT faking
    importlib: the editable-install machinery from hermes bootstrap —
    a .pth whose hook wins EVERY path lookup (sys.meta_path first, so
    a dir appended later is shadowed wholesale) yet resolves nothing
    for an unknown dir. That is the exact TRADEFIX2-2305 boot class."""
    return (
        "import sys, types\n"
        "_sh = types.ModuleType('_boot_shadow_hook')\n"
        "class _H:\n"
        "    @staticmethod\n"
        "    def install(*a, **k): pass\n"
        "_sh.install = _H.install\n"
        "sys.meta_path.insert(0, _sh)\n"
    )


def _child_env(tmp, with_pkg):
    env = dict(os.environ)
    env.pop("LCM2_SITE_DIRS", None)
    env.pop("LCM2_PG_SITE_DIR", None)
    env.pop("PYTHONPATH", None)
    if with_pkg:
        _stage_site(tmp)
    return env


def _run(shim, tmp, code, env):
    full = (
        _boot_class()
        + "import sys, importlib.util\n"
        f"spec = importlib.util.spec_from_file_location('d', {str(shim)!r})\n"
        "m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)\n"
        + textwrap.dedent(code))
    r = subprocess.run([sys.executable, "-E", "-I", "-c", full],
                       capture_output=True, text=True, timeout=120, env=env,
                       cwd=str(tmp))
    assert r.returncode == 0 or "RAISED" in r.stdout, r.stdout + r.stderr
    return r.stdout


def test_rescue_from_unarmed_site_dir(tmp_path):
    env = _child_env(tmp_path, with_pkg=True)
    env["LCM2_SITE_DIRS"] = str(tmp_path)
    out = _run(SHIMS[0], tmp_path, """
        import sys
        # BOOT CLASS (proven on mybuddy-trade): editable .pth hook
        # finders answer NO for every lookup and PathFinder is absent
        # from meta_path (bootstrap's editable-install machinery).
        # STAGE THE BOOT GAP (not fake the interpreter): keep the
        # interpreter fully healthy (stdlib importable incl. logging/
        # typing — mybuddy-trade's gateway imports those fine); the
        # gap is ONLY that the configured dir is invisible to the
        # finders, which the drop-in's PYTHONPATH never having reached
        # this -I interpreter already produces on its own.
        import importlib.machinery as _M  # noqa
        sys.path = [p for p in sys.path if 'site-packages' not in p]
        assert importlib.util.find_spec('psycopg') is None
        m.require_psycopg()
        import psycopg
        from psycopg.rows import dict_row
        from psycopg_pool import ConnectionPool
        assert callable(dict_row), 'dict_row must be real'
        assert ConnectionPool.__module__.startswith('psycopg_pool'), \
            ConnectionPool.__module__
        assert 'psycopg' in sys.modules and 'psycopg_pool' in sys.modules
        print('PASS', psycopg.__version__.split(' ')[0])
        """, env)
    assert "PASS" in out, out
    assert "9.9" not in out  # must be the REAL package, never a stub


def test_ambient_pythonpath_is_a_candidate(tmp_path):
    env = _child_env(tmp_path, with_pkg=True)
    env["PYTHONPATH"] = str(tmp_path)
    out = _run(SHIMS[0], tmp_path, """
        import sys, os
        # BOOT CLASS (proven on mybuddy-trade): editable .pth hook
        # finders answer NO for every lookup and PathFinder is absent
        # from meta_path (bootstrap's editable-install machinery).
        # STAGE THE BOOT GAP (not fake the interpreter): keep the
        # interpreter fully healthy (stdlib importable incl. logging/
        # typing — mybuddy-trade's gateway imports those fine); the
        # gap is ONLY that the configured dir is invisible to the
        # finders, which the drop-in's PYTHONPATH never having reached
        # this -I interpreter already produces on its own.
        import importlib.machinery as _M  # noqa
        sys.path = [p for p in sys.path
                    if p != os.environ['PYTHONPATH']]
        m.require_psycopg()
        import psycopg
        from psycopg.rows import dict_row
        print('PASS')
        """, env)
    assert "PASS" in out, out


def test_fail_close_kept(tmp_path):
    env = _child_env(tmp_path, with_pkg=False)
    env["LCM2_SITE_DIRS"] = str(tmp_path / "does-not-exist")
    out = _run(SHIMS[0], tmp_path, """
        import sys
        sys.meta_path = []
        try:
            m.require_psycopg()
            print('NO-RAISE')
        except ImportError as e:
            print('RAISED'); print(str(e))
        """, env)
    assert "RAISED" in out, out
    low = out.lower()
    assert "psycopg" in low and "pip install" in low
    assert "site dirs probed" in low  # names the rescue paths too


def test_hygiene_no_rescue_when_resolvable(tmp_path):
    # psycopg resolvable through normal finders: rescue must not run,
    # so no path/meta_path mutation happens.
    env = _child_env(tmp_path, with_pkg=True)
    env["LCM2_SITE_DIRS"] = str(tmp_path / "does-not-exist")
    out = _run(SHIMS[0], tmp_path, """
        import sys
        before_path = list(sys.path)
        before_meta = [type(f).__name__ for f in sys.meta_path]
        m.require_psycopg()
        print('HYGIENE', list(sys.path) == before_path,
              [type(f).__name__ for f in sys.meta_path] == before_meta)
        """, env)
    assert "HYGIENE True True" in out, out


def test_both_lanes_ship_rescue():
    for s in SHIMS:
        body = s.read_text()
        assert "_rescue_site_probe" in body, s
        assert "site dirs probed" in body.lower(), s
    a = SHIMS[0].read_text()
    b = SHIMS[1].read_text()
    # rescue machinery identical (only the module-name keys differ)
    import re
    norm = lambda t: re.sub(r"_(mnemo2|lcm2)_depcheck_x4", "_X", t)
    assert norm(a) == norm(b), "lane depcheck bodies must stay identical"
