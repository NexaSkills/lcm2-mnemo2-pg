"""F1 boot-crash regression: register() must leave ZERO residue.

The host boot crash ("No module named tools.transcription_common;
'tools' is not a package") happened because register()'s
clear()/update() restore only reinstates PRE-EXISTING bindings: any
sys.modules key our exec CREATED (a bare `tools` published by an
exec-time anchor, pgconn/ddl/schemas, _lcm2_impl_*, _lcm2_pg*) survived
and the host's later `import tools.<sub>` resolved against our flat
module. register() now diffs the key set and purges what it created.
"""
import importlib.util
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))


class _Ctx:
    def __getattr__(self, _n):
        def _f(*a, **k):
            return None
        return _f


def _load_pkg():
    name = "_f1_purge_pkg"
    saved = sys.modules.pop(name, None)
    spec = importlib.util.spec_from_file_location(
        name, os.path.join(HERE, "__init__.py"),
        submodule_search_locations=[HERE])
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    try:
        spec.loader.exec_module(mod)
    finally:
        if saved is not None:
            sys.modules[name] = saved
    return mod


def test_register_creates_no_surviving_module_keys():
    pkg = _load_pkg()
    before = {k for k, v in sys.modules.items()
              if isinstance(getattr(v, "__file__", None), str)
              and os.path.dirname(os.path.realpath(v.__file__))
              == os.path.realpath(HERE)}
    try:
        pkg.register(_Ctx())
    except Exception:
        pass  # psycopg may be absent; the finally-purge must run anyway
    ours = [k for k, v in sys.modules.items()
            if k not in before
            and isinstance(getattr(v, "__file__", None), str)
            and os.path.dirname(os.path.realpath(v.__file__))
            == os.path.realpath(HERE)]
    assert ours == [], f"register() left module keys behind: {ours}"
    assert "tools" not in ours, "register() must not create a bare 'tools'"
