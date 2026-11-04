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


def pytest_ignore_collect(collection_path, config):
    # SUITEGREEN-MIKE-0810: scratch/ is gitignored debris, incl.
    # scratch/quarantine/<profile>/ snapshots of FULL hermes profiles whose
    # skills/ ship their own tests/ dirs. Bare `pytest` (rootdir=repo) used
    # to collect those files under a DIFFERENT rootdir/conftest chain than
    # the snapshots were authored for -> import-mismatch collection errors.
    # Never collect quarantined profiles or scratch trees, under any
    # invocation form.
    p = str(collection_path)
    root = str(config.rootpath)
    if root and (p + "/").startswith(root + "/scratch/"):
        return True
    return None


def pytest_fixture_setup(request):
    # Before any schema-minting fixture runs, public must be empty in BOTH
    # dbs (mnemo's shared-view bake repopulates public live otherwise).
    try:
        name = getattr(request, "fixturename", None) or \
            getattr(getattr(request, "fixturedef", None), "argname", "")
    except Exception:
        name = ""
    if name in ("dsn", "priv_schema", "shared_schema", "mnemo_schema", "schema"):
        suite_prep.sweep_both("pre-ddl")
