"""F1 boot-crash regression: register() must leave ZERO residue.

The host boot crash ("No module named tools.transcription_common;
'tools' is not a package") happened because our import machinery served
a HOST thread's concurrent `import tools` our flat file, and/or left
module keys our exec CREATED surviving in sys.modules. register() now
purges created keys and the sibling finder is origin-gated.

Run with the host repo importable (like the real boot); otherwise the
bridge is None by design (_load_bridge_quietly contract) and the
register() contract is still fully exercised.
"""
import importlib.util
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
HOST = os.path.expanduser("~/.hermes/hermes-agent")


class _Ctx:
    def register_tool(self, *a, **k):
        pass

    def register_hook(self, *a, **k):
        pass

    def register_command(self, *a, **k):
        pass

    def register_context_engine(self, *a, **k):
        pass

    def register_skill(self, *a, **k):
        pass

    def __getattr__(self, _n):
        return lambda *a, **k: None


def _dirs_eq(a, b):
    try:
        return os.path.realpath(a) == os.path.realpath(b)
    except Exception:
        return False


def _load_pkg():
    name = "_f1_purge_pkg"
    saved = sys.modules.pop(name, None)
    sp_saved = list(sys.path)
    # real-boot emulation: host repo importable (bridge needs it)
    if os.path.isdir(HOST) and HOST not in sys.path:
        sys.path.append(HOST)
    try:
        spec = importlib.util.spec_from_file_location(
            name, os.path.join(HERE, "__init__.py"),
            submodule_search_locations=[HERE])
        mod = importlib.util.module_from_spec(spec)
        sys.modules[name] = mod
        try:
            spec.loader.exec_module(mod)
        except Exception:
            pass  # bridge/host deps may be absent; register() is the point
        return mod
    finally:
        sys.path[:] = sp_saved
        if saved is not None:
            sys.modules[name] = saved


def _ours_keys():
    return {k for k, v in sys.modules.items()
            if isinstance(getattr(v, "__file__", None), str)
            and _dirs_eq(os.path.dirname(v.__file__), HERE)}


def test_register_creates_no_surviving_module_keys():
    pkg = _load_pkg()
    before = _ours_keys()
    try:
        pkg.register(_Ctx())
    except Exception:
        pass  # missing DB/psycopg must NOT skip the finally-purge
    leaked = _ours_keys() - before
    assert "tools" not in leaked, (
        "register() must not leave a bare 'tools' bound to our file")
    assert leaked == set(), f"register() left module keys behind: {leaked}"
