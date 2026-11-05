"""P4-C: consolidation (engine.sleep parity + session-end invoker).

Live-instance tests: sleep marks consolidated_at, keeps originals,
writes episodic + embedding + log rows, and NEVER re-consolidates.
The session-end invoker is default-OFF and budget-guarded.
"""
import sys
import pathlib

import pytest

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "plugins" / "mnemo-pg2"))

import engine as engine_mod  # noqa: E402
import llm  # noqa: E402
import memory_provider as mp  # noqa: E402
from engine import Engine  # noqa: E402


@pytest.fixture()
def eng(live):
    return Engine()


@pytest.fixture(scope="module")
def live():
    try:
        e = Engine()
        with e._cx("private") as cx:
            cx.query_scalar("select 1")
    except Exception as exc:  # pragma: no cover - env guard
        pytest.skip(f"mnemo2 instance unreachable: {exc}")
    return True


@pytest.fixture()
def seed(eng):
    ids = []
    with eng._cx("private") as cx:
        for i in range(3):
            mid = f"mem_p4cseed{i}"
            cx.execute(
                "insert into working_memory(id,content,source,importance,"
                "session_id) values (%s,%s,'user',0.4,'p4c-sess')"
                " on conflict (id) do nothing",
                (mid, f"P4C seed fact number {i}: the vault rotates at "
                      f"dawn and logs to ledger {1000+i}"))
            ids.append(mid)
    yield ids
    with eng._cx("private") as cx:
        cx.execute("delete from working_memory where id = any(%s)", (ids,))
        cx.execute("delete from memory_embeddings where memory_id in"
                   " (select id from episodic_memory where"
                   " session_id='p4c-sess')")
        cx.execute("delete from episodic_memory where session_id='p4c-sess'")


def test_llm_failsoft_without_key(monkeypatch):
    for k in ("MNEMO2_LLM_KEY", "HERMES_CUSTOM_AI_NEXASKILLS_CO_ZA_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    assert llm.summarize("anything") is None


def test_sleep_marks_and_never_repeats(eng, seed):
    with eng._cx("private") as cx:
        # backdate so the default age gate would also pass
        cx.execute("update working_memory set created_at = created_at -"
                   " interval '48 hours' where id = any(%s)", (seed,))
        before = cx.query_scalar(
            "select count(*) from episodic_memory where"
            " session_id='p4c-sess'")
    res = eng.sleep()  # NOT force: exercises the 24h gate too
    assert res["ok"]
    with eng._cx("private") as cx:
        after = cx.query_scalar(
            "select count(*) from episodic_memory where"
            " session_id='p4c-sess'")
        assert after > before
        marked = cx.query_scalar(
            "select count(*) from working_memory where consolidated_at"
            " is not null and id = any(%s)", (seed,))
        assert marked == 3
        kept = cx.query_scalar(
            "select count(*) from working_memory where content is not"
            " null and id = any(%s)", (seed,))
        assert kept == 3          # lossless: originals intact
        emb = cx.query_scalar(
            "select count(*) from memory_embeddings me join"
            " episodic_memory e on e.id=me.memory_id where me.kind="
            "'episodic' and e.session_id='p4c-sess'")
        assert emb >= 1
    r2 = eng.sleep()
    with eng._cx("private") as cx:
        after2 = cx.query_scalar(
            "select count(*) from episodic_memory where"
            " session_id='p4c-sess'")
    assert after2 == after       # markers exclude: no doubles


def test_session_end_default_off(eng, seed, monkeypatch):
    monkeypatch.delenv("MNEMO2_AUTO_CONSOLIDATE", raising=False)
    prov = mp._make_provider_module().provider
    with eng._cx("private") as cx:
        before = cx.query_scalar(
            "select count(*) from working_memory where consolidated_at"
            " is not null and id = any(%s)", (seed,))
    prov.on_session_end([])
    with eng._cx("private") as cx:
        after = cx.query_scalar(
            "select count(*) from working_memory where consolidated_at"
            " is not null and id = any(%s)", (seed,))
    assert after == before


def test_session_end_small_bank_budget_skip(eng, seed, monkeypatch):
    monkeypatch.setenv("MNEMO2_AUTO_CONSOLIDATE", "1")
    prov = mp._make_provider_module().provider
    # fresh gate + tiny eligible set (<10) -> dry-run guard skips sleep
    with eng._cx("private") as cx:
        before = cx.query_scalar(
            "select count(*) from working_memory where consolidated_at"
            " is not null")
    prov.on_session_end([])
    with eng._cx("private") as cx:
        after = cx.query_scalar(
            "select count(*) from working_memory where consolidated_at"
            " is not null")
    assert after == before
