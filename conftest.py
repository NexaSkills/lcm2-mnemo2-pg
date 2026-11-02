import pathlib
import sys

import suite_prep  # noqa: F401  (session start/end sweeps, both dbs)

_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT)) if str(_ROOT) not in sys.path else None

# hooks must live in a top-level-named conftest (rootdir conftests are scanned
# for plugin hooks before collection). Both suites' dbs get the pre-ddl sweep.


def _this_db():
    return "lcm2db" if "lcm2db" in str(getattr(sys.modules.get("conftest"), "BASE_DSN", "")) else None


def pytest_fixture_setup(request, tmp_path_factory):
    # Before any schema-minting fixture runs, public must be empty in BOTH
    # dbs (mnemo's shared-view bake repopulates public live otherwise).
    if request.fixturedef.argname in ("dsn", "priv_schema", "shared_schema",
                                      "mnemo_schema", "schema"):
        suite_prep.sweep_both("pre-ddl")
