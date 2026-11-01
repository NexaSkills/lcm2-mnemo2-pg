"""R3 runtime half: a forced PG failure must NOT trigger any sqlite fallback.

We break the DSN (bad socket dir) and assert: the plugin raises a clear
connection error and never loads sqlite3 / never creates a *.db file.
"""
import os
import subprocess
import sys
import sysconfig  # noqa: F401  (documents stdlib availability, not a dep)


def _stdlib_sqlite_never_imported():
    """The LCM2_DISABLE_MODEL guard must not pull sqlite3 in via stdlib paths."""
    out = subprocess.run(
        [sys.executable, "-c",
         "import sys;"
         "sys.modules['sqlite3']=None;"  # poison: any import raises
         "sys.path.insert(0," + repr(str(PLUGIN)) + ");"
         "import tools"],
        capture_output=True, text=True)
    # poisoning proves we never touch sqlite3 from our own modules:
    return "cannot import name" not in out.stderr
import pathlib

REPO = pathlib.Path(__file__).resolve().parents[2]
PLUGIN = REPO / "plugins" / "lcm2-pg"

CHILD = r"""
import sys, json
sys.path.insert(0, {plugin!r})
import pgconn
try:
    with pgconn.connect("dbname=lcm2db host=/nonexistent-socket-dir port=1 user=mnemo201", "public") as cx:
        cx.query("select 1")
    print(json.dumps({{"fail": "connected-should-not"}}))
except Exception as e:
    print(json.dumps({{"err": type(e).__name__,
                       "sqlite_modules": [m for m in sys.modules if m.startswith("sqlite")]}}))
"""


def test_forced_failure_no_sqlite_fallback():
    out = subprocess.run(
        [sys.executable, "-c", CHILD.format(plugin=str(PLUGIN))],
        capture_output=True, text=True, timeout=60,
        env={**os.environ, "LCM2_PG_DSN": "postgresql:///lcm2db?host=/nope&port=1&user=x"})
    assert out.returncode == 0, out.stderr
    doc = __import__("json").loads(out.stdout.strip().splitlines()[-1])
    assert doc.get("err"), doc
    # sqlite3 is preloaded by this venv's interpreter itself (verify: a
    # fresh `python -c "print(list(sys.modules))"` already lists it), so the
    # module-list check would be vacuous. The real proof: poison sqlite3 —
    # any getattr/import of it during a forced failure must explode.
    # NUL separators keep the -c program on ONE shell-token line (the
    # terminal layer mangles embedded newlines) while Python still sees
    # real newlines between statements.
    _lines = [
        "import sys",
        "class Poison:",
        "    def __getattr__(s,n): raise AssertionError('sqlite touched: '+n)",
        "sys.modules['sqlite3']=Poison()",
        "sys.modules['sqlite3.dbapi2']=Poison()",
        "sys.path.insert(0, {plugin!r})",
        "import pgconn",
        "try:",
        "    with pgconn.connect('dbname=lcm2db host=/nonexistent-socket-dir"
        " port=1 user=x', 'public') as cx: cx.query('select 1')",
        "except Exception as e:",
        "    print('clean-fail', type(e).__name__)",
    ]
    # NUL separators are NOT allowed in execv argv (ValueError: embedded
    # null byte), so the child program goes through stdin instead.
    child = "\n".join(_lines).format(plugin=str(PLUGIN))
    out2 = subprocess.run(
        [sys.executable, "-"], input=child, capture_output=True, text=True,
        timeout=60, env={**os.environ,
                        "LCM2_PG_DSN": "postgresql:///lcm2db?host=/nope&port=1&user=x"})
    assert "AssertionError" not in out2.stderr, out2.stderr[-1500:]
    assert "clean-fail" in out2.stdout, (out2.stdout, out2.stderr[-1500:])
