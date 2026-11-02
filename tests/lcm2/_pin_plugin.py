"""fix6 N-3: pytest plugin that pins THE BARE shared module keys to
lcm2-pg's own files for the whole lcm2-suite execution window — and
leaves them byte-untouched outside it.

Why a plugin (not a fixture): the failing imports (`import engine` inside
test_r4_tools_surface's fixture setup, `import pgconn` at test-module
top level) can fire before/around autouse fixture boundaries, and the
sibling suite's _suite_imports installs delegating proxies AT THE BARE
KEYS (sys.modules['engine'] -> mnemo's file via __getattr__). A fixture
that only flips a meta_path finder can't win when the proxy module is
already cached under the bare key: `import engine` never consults
finders — sys.modules hits first. The only deterministic lever is the
bare KEY itself.

Discipline (F-1 symmetry — the same rule the plugins obey):
* nothing is published or removed unless we own the name (the binding
  resolves into plugins/lcm2-pg) or we created the current binding,
* the pre-window binding object is restored verbatim after every test
  (identity, not "an equivalent module"),
* no sys.path edits here; the sibling suite owns path games.

mnemo2's own tests therefore see exactly the interpreter they'd see in an
isolated run: outside lcm2's window we never touch their keys.
"""
import os as _os
import sys as _sys

_DIR = _os.path.dirname(_os.path.abspath(__file__))            # tests/lcm2
_OUR_PLUGIN = _os.path.join(_os.path.dirname(_os.path.dirname(_DIR)),
                            "plugins", "lcm2-pg")
SHARED = ("engine", "pgconn", "ddl", "plugin_entry", "embed",
          "context_engine_shim", "memory_provider", "tools", "hooks",
          "schemas", "command")

_saved = {}          # bare name -> pre-window binding (restored after test)


def _ours_bound(name):
    m = _sys.modules.get(name)
    f = getattr(m, "__file__", None)
    if not isinstance(f, str):
        return False
    try:
        return _os.path.dirname(_os.path.realpath(f)) == _os.path.realpath(
            _OUR_PLUGIN)
    except Exception:
        return False


def _pin(name):
    """Bind the bare key to OUR file (namespaced twin first; publish bare
    only across the window we manage)."""
    twin = "_lcm2_pin." + name
    mod = _sys.modules.get(twin)
    if mod is None or not _ours_bound_key(twin):
        import importlib.util as _ilu
        path = _os.path.join(_OUR_PLUGIN, name + ".py")
        if not _os.path.isfile(path):
            return None
        spec = _ilu.spec_from_file_location(twin, path)
        if spec is None or spec.loader is None:
            return None
        mod = _ilu.module_from_spec(spec)
        _sys.modules[twin] = mod
        try:
            spec.loader.exec_module(mod)
        except BaseException:
            _sys.modules.pop(twin, None)
            raise
    if name not in _saved:
        _saved[name] = _sys.modules.get(name)
    _sys.modules[name] = mod
    return mod


def _ours_bound_key(name):
    return _ours_bound(name)


def _release():
    """Restore every pre-window binding byte-exactly (identity)."""
    for name, prev in _saved.items():
        if prev is None:
            if _ours_bound(name):
                _sys.modules.pop(name, None)
        else:
            _sys.modules[name] = prev
    _saved.clear()


def pytest_runtest_logstart(nodeid, location):
    if location[0] and location[0].startswith("tests/lcm2"):
        for n in SHARED:
            if not _ours_bound(n):
                _pin(n)


def pytest_runtest_logfinish(nodeid, location):
    if _saved:
        _release()
