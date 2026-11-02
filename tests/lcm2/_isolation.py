"""Session isolation pin (qa3 N-3 combined run): every test runs with
sys.path[0] == ITS OWN suite dir, so a bare `import conftest` /
`import engine` inside a test binds that test's own plugin's files.

WHY: the suites share one pytest process when run as `pytest tests`. The
import-lane modules (_suite_imports proxies, _public_imports anchors) are
correct in-process, but pytest's own rootdir/first-contrib-import ordering
can leave sys.path[0] pointing at the SIBLING suite's dir for tests of the
other dir — then lcm2's `import engine` (conftest registered fixture)
silently answers mnemo-pg2's engine ('module _mnemo2_impl_engine has no
attribute LCM2Engine'), r8's connect() binds mnemo2's pgconn
('unexpected keyword set_session') and r4_table_parity's ddl_log Conn
vanishes. Pinning the dir per test makes the combined run order-equivalent
to the two isolated runs; no sys.modules keys are touched (host hygiene)."""
import pathlib

import pytest

_DIR = str(pathlib.Path(__file__).resolve().parent)


@pytest.fixture(autouse=True)
def _own_dir_first():
    sys = __import__("sys")
    saved = list(sys.path)
    if sys.path[:1] != [_DIR]:
        if _DIR in sys.path:
            sys.path.remove(_DIR)
        sys.path.insert(0, _DIR)
    yield
    sys.path[:] = saved
