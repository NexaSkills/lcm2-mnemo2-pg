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
