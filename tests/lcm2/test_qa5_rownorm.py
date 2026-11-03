"""qa5 row-shape regression: _public_tables must tolerate dict rows AND
plain-tuple rows. Root cause of the combined-suite FAIL: the landing leg
indexed r["tablename"] directly; when the bound pgconn's connection uses a
tuple row-factory (sibling pgconn bound first in combined ordering) the
readback crashed with 'string indices must be integers'. Tuple rows crash
on ANY dict-indexing path, so pinning that contract here reproduces the
failure mode and proves the normalization."""
import importlib.util as ilu
import pathlib

import pytest

_ROOT = pathlib.Path(__file__).resolve().parents[2]
_SUBJECT = _ROOT / "tests" / "lcm2" / "test_qa5_public_tripwire.py"


class _Fake:
    def __init__(self, rows):
        self._rows = rows

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def query_dicts(self, _sql, _p=None):
        return self._rows


class _FakePg:
    def __init__(self, rows):
        self._rows = rows

    def connect(self, *a, **k):
        return _Fake(self._rows)


def _subject():
    key = "qa5_rownorm_subject"
    import sys
    if key not in sys.modules:
        spec = ilu.spec_from_file_location(key, str(_SUBJECT))
        assert spec and spec.loader
        mod = ilu.module_from_spec(spec)
        sys.modules[key] = mod
        spec.loader.exec_module(mod)
    return sys.modules[key]


@pytest.mark.parametrize("rows", [
    [{"tablename": "a"}, {"tablename": "b"}],   # dict_row factory
    [("a",), ("b",)],                            # plain-tuple factory
    [{"tablename": "a"}, ("b",)],                # mixed
])
def test_public_tables_row_shape(rows):
    mod = _subject()
    assert mod._public_tables(_FakePg(rows), "x-dsn") == {"a", "b"}
