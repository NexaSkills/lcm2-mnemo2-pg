"""P3-FIX regression: plugin_entry loads via BOTH entry routes.

The envbind end (fix2 brief F2 / tessa H-5): no product module may
depend on an import that only works from ONE load path. plugin_entry
imports its siblings bare (`import engine`, `import pgconn`) — that is
served by the package loader (mnemo-pg2/__init__ + _selfimport.anchor)
on the host route, and by sys.path on the direct-by-path route the
tests/probes use. envbind.py itself is OUT of product code (option b);
the per-agent env binding lives inside plugin_entry._envbind_ctx, and
pgconn consults its cell via sys.modules KEY only — so neither module
imports the other at load time (no circular import on either route).

Routes proven here (each in a FRESH subprocess — module-identity
questions cannot be proven inside a process the suite already polluted):
  1. normal import: load mnemo-pg2 as a package via spec_from_file_location
     on __init__.py (the host route), call register() on a stub ctx;
  2. load-by-path: spec_from_file_location directly on plugin_entry.py
     with the plugin dir on sys.path (the tests/probes route), call
     register();
  3. pgconn alone: import pgconn.py by path with NO plugin_entry loaded
     — resolves the ambient env, must not import error.
"""
import importlib.util
import os
import pathlib
import subprocess
import sys

REPO = pathlib.Path(__file__).resolve().parents[2]
PLUGIN_DIR = REPO / "plugins" / "mnemo-pg2"

_STUB = '''
class Ctx:
    def __init__(self):
        self.tools, self.hooks = {}, {}
    def register_tool(self, name, toolset, schema, handler, description=""):
        self.tools[name] = handler
    def register_hook(self, event, fn):
        self.hooks.setdefault(event, []).append(fn)
'''


def _run(child):
    env = dict(os.environ)
    env.pop("MNEMO2_TOOLS", None)
    r = subprocess.run([sys.executable, "-c", child], capture_output=True,
                       text=True, timeout=120, env=env, cwd=str(REPO))
    assert r.returncode == 0, (
        f"child exited {r.returncode}\nSTDOUT: {r.stdout[-2000:]}\n"
        f"STDERR: {r.stderr[-4000:]}")
    return r.stdout


def test_plugin_entry_loads_via_normal_package_import():
    """Host route: package import of __init__.py -> register() -> 42 tools."""
    child = f"""
import importlib.util, sys
{_STUB}
spec = importlib.util.spec_from_file_location(
    "mnemo_pg2_normal_pkg", {str(PLUGIN_DIR / "__init__.py")!r},
    submodule_search_locations=[{str(PLUGIN_DIR)!r}])
mod = importlib.util.module_from_spec(spec)
mod.__package__ = "mnemo_pg2_normal_pkg"
mod.__path__ = [{str(PLUGIN_DIR)!r}]
sys.modules["mnemo_pg2_normal_pkg"] = mod
spec.loader.exec_module(mod)
assert hasattr(mod, "register"), "no register() on package import"
ctx = Ctx()
res = mod.register(ctx)
assert res["tools"] >= 42, res
assert len(ctx.tools) >= 42, len(ctx.tools)
assert len(ctx.hooks) == 3, sorted(ctx.hooks)
print("OK-NORMAL")
"""
    assert "OK-NORMAL" in _run(child)


def test_plugin_entry_loads_direct_by_path():
    """Probes/tests route: plugin_entry.py itself by spec_from_file_location
    (NO package wrapper) + register() on the same surface."""
    child = f"""
import importlib.util, sys
{_STUB}
sys.path.insert(0, {str(PLUGIN_DIR)!r})
spec = importlib.util.spec_from_file_location(
    "mnemo_pg2_bypath_entry", {str(PLUGIN_DIR / 'plugin_entry.py')!r})
mod = importlib.util.module_from_spec(spec)
sys.modules["mnemo_pg2_bypath_entry"] = mod
spec.loader.exec_module(mod)
assert hasattr(mod, "register"), "no register() on by-path load"
ctx = Ctx()
res = mod.register(ctx)
assert res["tools"] >= 42, res
assert len(ctx.tools) >= 42, len(ctx.tools)
assert len(ctx.hooks) == 3, sorted(ctx.hooks)
print("OK-BYPATH")
"""
    assert "OK-BYPATH" in _run(child)


def test_pgconn_loads_standalone_without_plugin_entry():
    """envbind option b: pgconn must import with NO plugin_entry/envbind
    ever loaded (the binding cell is consulted by sys.modules key and is
    simply absent -> ambient env)."""
    child = f"""
import importlib.util, sys
spec = importlib.util.spec_from_file_location(
    "mnemo_pg2_bypath_pgconn", {str(PLUGIN_DIR / 'pgconn.py')!r})
mod = importlib.util.module_from_spec(spec)
sys.modules["mnemo_pg2_bypath_pgconn"] = mod
spec.loader.exec_module(mod)
assert mod._envget("MNEMO2_DEFINITELY_UNSET_XYZ", "fallback") == "fallback"
assert "mnemo_pg2_bypath_entry" not in sys.modules
print("OK-PGCONN")
"""
    assert "OK-PGCONN" in _run(child)


def test_product_has_no_envbind_module_reference():
    """Fix-close contract: envbind.py is removed and NOTHING in plugins/
    imports or references it as a module (the _envbind_ctx class and the
    sys.modules cell key are internal names, not module references)."""
    assert not (PLUGIN_DIR / "envbind.py").exists(), (
        "envbind.py must stay OUT of product code (fix2 F2 option b)")
    for f in PLUGIN_DIR.glob("*.py"):
        text = f.read_text(encoding="utf-8")
        for line in text.splitlines():
            s = line.strip()
            if s.startswith("#"):
                continue
            assert "import envbind" not in s, (f.name, line)
            # the OLD envbind module's PUBLIC API (names that only ever
            # existed on the deleted module) must never be referenced.
            # Internal names (_envbind_ctx class, the cell key) and prose
            # mentions in docstrings are fine — a docstring cannot import.
            assert not any(
                tok in s for tok in ("envbind.eff", "envbind.scoped",
                                     "envbind._tls", "envbind._envstr",
                                     'import_module("envbind',
                                     "import_module('envbind")), (
                    f.name, line)
