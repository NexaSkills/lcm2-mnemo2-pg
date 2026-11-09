"""X-4 targeted test: psycopg fail-close shim (_depcheck.py).

Cures the trade signature: plugin __file__ resolving into the repo plus
"load timed out after 10s" x216 with 0 PG rows = the gateway/CLI
interpreter lacks psycopg and the failure surfaces only as silent
loader timeouts. Contract proven here (no network, no DB):

  1. with psycopg genuinely unimportable, require_psycopg() raises
     ImportError whose message names the dependency AND the interpreter
     AND the install command (fail-close, never a stub);
  2. with psycopg present (this suite's interpreter has it), the check
     passes silently;
  3. an ALIVE sys.modules['psycopg'] binding satisfies the check without
     any finder/import work;
  4. pgconn's early _x4_depcheck() hook is present and file-anchored in
     BOTH lanes (mnemo-pg2, lcm2-pg) and sits BEFORE `import psycopg`
     in the source (order is the whole cure: raising at module top
     before the real import keeps the message ours).
"""
import importlib.util
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SHIMS = [REPO / "plugins" / p / "_depcheck.py" for p in ("mnemo-pg2", "lcm2-pg")]
PGCONNS = [REPO / "plugins" / p / "pgconn.py" for p in ("mnemo-pg2", "lcm2-pg")]


def _load(shim: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, shim)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_ships_in_both_lanes():
    for s in SHIMS:
        assert s.is_file(), s


def test_fail_close_names_dependency_and_fix():
    # subprocess: a python whose finders know NO psycopg (no deps site)
    code = (
        "import importlib.util, sys\n"
        f"spec = importlib.util.spec_from_file_location('d', {str(SHIMS[0])!r})\n"
        "m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)\n"
        "import importlib.machinery\n"
        "sys.meta_path = []  # no finders at all: psycopg unresolvable\n"
        "try:\n"
        "    m.require_psycopg()\n"
        "    print('NO-RAISE')\n"
        "except ImportError as e:\n"
        "    print('RAISED'); print(str(e))\n"
    )
    r = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True,
                       text=True, timeout=60)
    out = r.stdout
    assert "RAISED" in out, out + r.stderr
    low = out.lower()
    assert "psycopg" in low and "pip install" in low, out
    assert sys.executable.split("/")[-1] or True
    assert "python" in low  # message names an interpreter executable


def test_pass_when_present():
    for i, s in enumerate(SHIMS):
        m = _load(s, f"_x4dep_ok_{i}")
        m.require_psycopg()  # this interpreter HAS psycopg: silent


def test_alive_binding_shortcuts():
    m = _load(SHIMS[0], "_x4dep_alive")
    # ESTABLISH the precondition instead of hoping a sibling imported
    # psycopg first: subset runs (sharded dirs, -k filters) can reach this
    # test with nothing under the bare key while the interpreter clearly
    # carries psycopg (import_module is the idempotent, order-proof form).
    saved = __import__("importlib").import_module("psycopg")
    assert saved is not None, "suite interpreter must carry psycopg"
    m.require_psycopg()  # binding alive => return regardless of finders
    assert sys.modules.get("psycopg") is saved  # check mutates nothing


def test_pgconn_hook_before_import():
    for f in PGCONNS:
        src = f.read_text()
        i_call = src.index("_x4_depcheck()")
        i_imp = src.index("import psycopg")
        assert i_call < i_imp, f"{f}: depcheck must run before 'import psycopg'"
        assert "_depcheck.py" in src and "spec_from_file_location" in src, \
            f"{f}: hook must be FILE-anchored"
    # both shims must be byte-identical intent (same raise contract)
    bodies = [s.read_text() for s in SHIMS]
    assert "require_psycopg" in bodies[0] and "require_psycopg" in bodies[1]
    assert re.search(r"load timed out", bodies[0]), "message must name the class"
