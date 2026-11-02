import pathlib
import sys

_ROOT = pathlib.Path(__file__).resolve().parent
for _p in (str(_ROOT), str(_ROOT / "tests")):
    if _p not in sys.path:
        sys.path.insert(0, _p)
try:
    import suite_prep  # noqa: F401  (session start/end sweeps, both dbs)
except Exception:  # pragma: no cover
    class suite_prep:  # type: ignore  # noqa: N801
        @staticmethod
        def sweep_both(label=""):
            pass

# hooks must live in a top-level-named conftest (rootdir conftests are scanned
# for plugin hooks before collection). Both suites' dbs get the pre-ddl sweep.


def _this_db():
    return "lcm2db" if "lcm2db" in str(getattr(sys.modules.get("conftest"), "BASE_DSN", "")) else None


def pytest_fixture_setup(request):
    # Before any schema-minting fixture runs, public must be empty in BOTH
    # dbs (mnemo's shared-view bake repopulates public live otherwise).
    if request.fixturedef.argname in ("dsn", "priv_schema", "shared_schema",
                                      "mnemo_schema", "schema"):
        suite_prep.sweep_both("pre-ddl")
