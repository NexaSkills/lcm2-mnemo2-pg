"""qa4 N-3: deterministic bare-key accessors for the lcm2 suite.

`import engine` etc. is ORDER-SENSITIVE in a combined pytest process: the
sibling suite installs delegating proxies at the bare keys and its
register() republishes them between tests, so sys.modules lookups can hit
the sibling's file even inside lcm2's own window (the per-import re-pin
backstop in _pin_plugin covers test-body imports; fixture SETUP imports
run before that hook fires on some paths). The deterministic lever is the
import SYSTEM: a meta_path finder that answers the SHARED bare names from
plugins/lcm2-pg files — consulted on every import miss, independent of
sys.path order and of the sibling's proxies (a cached sys.modules entry
still wins for already-imported names, which is why the accessors below
route through `importlib.import_module` + a directory check, NOT the
bare attribute).

Never steals anything outside the SHARED name set; the finder restores
foreign bindings outside lcm2's window (set_active(False)).
"""
import importlib.util as _ilu
import os as _os
import sys as _sys

_DIR = _os.path.dirname(_os.path.abspath(__file__))
_OUR_PLUGIN = _os.path.normpath(_os.path.join(_DIR, "..", "..", "plugins", "lcm2-pg"))

SHARED = ("engine", "pgconn", "ddl", "plugin_entry", "embed",
          "context_engine_shim", "memory_provider", "tools", "hooks",
          "schemas", "command")

_ACTIVE = [False]


class _Lcm2FileFinder:
    """Answers SHARED bare names from lcm2-pg files while active."""
    _mark = "_lcm2_file_finder"

    def find_spec(self, fullname, path=None, target=None):
        if not _ACTIVE[0] or fullname not in SHARED:
            return None
        cur = _sys.modules.get(fullname)
        if cur is not None and _is_ours(cur):
            return None  # already ours — no work
        f = _os.path.join(_OUR_PLUGIN, fullname + ".py")
        if not _os.path.isfile(f):
            return None
        spec = _ilu.spec_from_file_location(fullname, f)
        if spec is not None:
            spec._loader_override = True
        return spec

    def __init__(self):  # marker-only; instance stored once
        pass


def _is_ours(mod):
    f = getattr(mod, "__file__", None)
    if not isinstance(f, str):
        return False
    try:
        return _os.path.dirname(_os.path.realpath(f)) == _os.path.realpath(_OUR_PLUGIN)
    except Exception:
        return False


def set_active(on):
    _ACTIVE[0] = bool(on)
    if on:
        if not any(getattr(f, "_mark", None) == _Lcm2FileFinder._mark
                   for f in _sys.meta_path):
            finder = _Lcm2FileFinder()
            _sys.meta_path.insert(0, finder)
    else:
        _sys.meta_path[:] = [f for f in _sys.meta_path
                             if getattr(f, "_mark", None) != _Lcm2FileFinder._mark]


def own(name):
    """Return OUR file for `name` bound deterministically (finder-assisted)."""
    set_active(True)
    try:
        if name in SHARED:
            # drop a foreign cached binding ONLY for the duration of this
            # call so the import system re-consults finders (we restore it
            # if OUR load fails; on success OUR module holds the key and
            # _pin_plugin's release restores the pre-window binding).
            cur = _sys.modules.get(name)
            foreign = cur is not None and not _is_ours(cur)
            if foreign:
                _sys.modules.pop(name, None)
            try:
                mod = _import_by_path(name)
                return mod
            except Exception:
                if foreign and cur is not None:
                    _sys.modules[name] = cur
                raise
        return __import__(name)
    finally:
        pass


def _import_by_path(name):
    """Load OUR file under the bare key by path (no finder needed)."""
    f = _os.path.join(_OUR_PLUGIN, name + ".py")
    spec = _ilu.spec_from_file_location(name, f)
    if spec is None or spec.loader is None:
        raise ImportError(f"no lcm2 file for {name}")
    mod = _sys.modules.get(name)
    if mod is not None and _is_ours(mod):
        return mod
    mod = _ilu.module_from_spec(spec)
    _sys.modules[name] = mod
    try:
        spec.loader.exec_module(mod)
    except BaseException:
        _sys.modules.pop(name, None)
        raise
    return mod
