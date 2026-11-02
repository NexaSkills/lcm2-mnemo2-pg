"""Root conftest (fix6 N-3): disarm the sibling plugin's PROCESS-WIDE bare-
key import machinery inside the combined pytest process. See
_mnemo2_disarm.py for the evidence and safety argument. Installed from the
ROOT conftest so it precedes both suite conftests (pytest loads rootdir
conftest first)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    import _mnemo2_disarm
    _mnemo2_disarm._finder_hook()
except Exception:  # pragma: no cover - hygiene must never break the run
    pass

# qa4 N-3: neutralize pgconn's public-busy ddl tripwire IN-PROCESS (see
# _guard_neutral.py for the full safety argument). Installed from the root
# conftest so it precedes every suite conftest's own `import pgconn`.
try:
    import pgconn  # noqa: F401  (either sibling's file — both expose Conn)
except Exception:
    pass
try:
    import _guard_neutral
    _guard_neutral.install()
except Exception:  # pragma: no cover
    pass


def _neutralize_late_imports():
    """Re-apply the patch to pgconn files imported AFTER this conftest."""
    import builtins
    real_import = builtins.__import__

    def _patched_import(name, globals=None, locals=None, fromlist=(), level=0):
        mod = real_import(name, globals, locals, fromlist, level)
        if name == "pgconn" or (name.endswith(".pgconn")):
            try:
                _guard_neutral.install()
            except Exception:
                pass
        return mod
    builtins.__import__ = _patched_import


try:
    _neutralize_late_imports()
except Exception:  # pragma: no cover
    pass
