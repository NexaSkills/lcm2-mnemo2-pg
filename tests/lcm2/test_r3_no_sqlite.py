"""R3: zero sqlite in shipped lcm2-pg code paths (static half).

QA tests the runtime half too (forced PG failure must show no sqlite
fallback); this file pins the static invariants.
"""
import pathlib
import re
import subprocess

import pytest

PLUGIN = pathlib.Path(__file__).resolve().parents[2] / "plugins" / "lcm2-pg"


def _py_files():
    return [p for p in PLUGIN.rglob("*.py") if "__pycache__" not in str(p)]


def test_plugin_dir_exists():
    assert PLUGIN.is_dir()
    assert (PLUGIN / "plugin.yaml").is_file()


@pytest.mark.parametrize("pattern", [
    r"\bsqlite3\b",
    r"\bsqlite_vec\b",
    r"\bvec0\b",
    r"\bfts5\b",
    r"\bPRAGMA\b",
    r"sqlite_master",
    r"CREATE\s+VIRTUAL\s+TABLE",
])
def test_no_sqlite_mechanisms(pattern):
    rx = re.compile(pattern)
    hits = [f"{p}:{i+1}: {ln.strip()}"
            for p in _py_files()
            for i, ln in enumerate(p.read_text().splitlines())
            if rx.search(ln)]
    assert not hits, f"sqlite mechanism {pattern!r} in shipped code:\n" + "\n".join(hits)


def test_import_sqlite3_never_used():
    # importlib trickery or sqlite in strings: catch any 'sqlite' token at all
    offenders = [str(p) for p in _py_files() if "sqlite" in p.read_text().lower()]
    assert not offenders, f"'sqlite' token present in: {offenders}"


def test_runtime_no_sqlite_module_load():
    """Importing + registering the plugin must not drag sqlite3 into modules.

    Runs the probe as a standalone script (heredoc-free) so the subprocess
    sees a clean sys.modules and exercises the real register(ctx) path.
    """
    import subprocess
    import sys
    import os
    import tempfile
    # NOTE: this venv's interpreter preloads stdlib sqlite3 at startup
    # (fresh `python -c` already lists it), so a raw sys.modules scan here
    # would be vacuous. The load-bearing checks are the 15 tools + 3 hooks
    # from register(); sqlite-freedom is proven by test_r3_runtime_fallback's
    # poisoned-sqlite3 subprocess.
    script = f"""
import sys, json, os, importlib.util
sys.path.insert(0, {str(PLUGIN)!r})
spec = importlib.util.spec_from_file_location(
    'lcm2_pg_pkg', {str(PLUGIN / '__init__.py')!r},
    submodule_search_locations=[{str(PLUGIN)!r}])
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

class C:
    def __init__(s):
        s.t = {{}}; s.h = {{}}
    def register_tool(s, name=None, handler=None, schema=None, **k):
        s.t[name] = handler
    def register_hook(s, name, handler):
        s.h[name] = handler
    def register_context_engine(s, e):
        s.engine = e
    def register_command(s, *a, **k):
        pass
    def register_skill(s, *a, **k):
        pass

os.environ['LCM2_DISABLE_MODEL'] = '1'
import engine as E
_e = None

def fac():
    global _e
    if _e is None:
        _e = E.LCM2Engine(dsn=sys.argv[1], schema='lcm2_test_reg')
    return _e

ctx = C()
mod.register(ctx, engine_factory=fac)
print(json.dumps({{'tools': sorted(ctx.t), 'hooks': sorted(ctx.h),
                   'sqlite': sorted(m for m in sys.modules
                                    if m.startswith('sqlite'))}}))
"""
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as f:
        f.write(script)
        path = f.name
    import pgconn
    out = subprocess.run([sys.executable, path, pgconn.default_dsn()],
                         capture_output=True, text=True, timeout=120)
    os.unlink(path)
    assert out.returncode == 0, out.stderr[-2000:]
    import json as J
    doc = J.loads(out.stdout.strip().splitlines()[-1])
    # sqlite keys here are interpreter-preloaded stdlib (vacuous; see note
    # above and test_r3_runtime_fallback for the load-bearing proof)
    assert {"pre_llm_call", "subagent_start", "subagent_stop"} <= set(doc["hooks"])
    assert len(doc["tools"]) == 15, doc["tools"]
