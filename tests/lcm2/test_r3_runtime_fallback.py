"""R3 runtime half: a forced PG failure must NOT trigger any sqlite fallback.

We break the DSN (bad socket dir) and assert: the plugin raises a clear
connection error and never loads sqlite3 / never creates a *.db file.
"""
import os
import subprocess
import sys
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
    assert doc.get("sqlite_modules") == [], doc
    assert doc.get("err"), doc
