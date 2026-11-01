"""Shared fixtures for lcm2-pg tests.

Everything runs against the REAL dedicated instance (socket
~/lcm2-mnemo2-pg/pg/run, port 54331, db lcm2db) over the SAME connection
path the plugin uses in production. No sqlite anywhere (R3).
"""
import os
import sys
import pathlib

import pytest

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "plugins" / "lcm2-pg"))

import pgconn  # noqa: E402  (plugin module)

SOCKET = os.environ.get(
    "LCM2_PG_HOST", os.path.expanduser("~/lcm2-mnemo2-pg/pg/run"))
DB = "lcm2db"
TEST_SCHEMA = "lcm2_test_probe"


def psql_ok() -> bool:
    try:
        with pgconn.connect(f"dbname={DB} host={SOCKET} port=54331 user=mnemo201",
                            TEST_SCHEMA) as cx:
            return cx.query_scalar("select 1") == 1
    except Exception:
        return False


@pytest.fixture(scope="session")
def dsn():
    assert psql_ok(), (
        f"lcm2 instance not reachable at {SOCKET}:54331 db={DB} "
        "- start it (scripts/ensure_instance.sh); tests never fall back (R3)")
    return f"dbname={DB} host={SOCKET} port=54331 user=mnemo201"


@pytest.fixture
def schema(dsn):
    """Fresh throwaway schema per test, dropped afterwards."""
    import uuid
    name = "lcm2_test_" + uuid.uuid4().hex[:10]
    with pgconn.connect(f"dbname={DB} host={SOCKET} port=54331 user=mnemo201",
                        "public", ddl=True) as cx:
        cx.execute(f'CREATE SCHEMA "{name}"')
    yield name
    with pgconn.connect(f"dbname={DB} host={SOCKET} port=54331 user=mnemo201",
                        "public", ddl=True) as cx:
        cx.execute(f'DROP SCHEMA "{name}" CASCADE')
