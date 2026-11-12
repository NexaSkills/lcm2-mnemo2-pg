"""P4 false-failure class (tutor 2026-11-12, state.db msgs 2879-2886).

An agent wanted to SET a task and passed state= + metadata= but omitted
action. The old engine silently ran the get branch (action default
"get"), ignored the write fields, and returned ok:false on the
nonexistent name — which Hermes renders as a TOOL FAILURE. The agent
retried four times with guessed names and the task never persisted.

Contract proven here (the fix):
  1. state (or metadata) present + action omitted  -> inferred set
     (the write lands; ok:true, state echoed).
  2. get on an unknown task name -> ok:true, found:false, state:null,
     plus an actionable hint. Absence is INFORMATION, not failure;
     ok:false is reserved for genuine errors (no task name, bad action).
  3. metadata arriving as a JSON string is stored parsed (dict), not
     double-encoded prose.
"""
import json
import uuid

import tests.mnemo2.conftest as _c  # qualified: never the sibling suite's


def _name(prefix):
    return f"{prefix} {uuid.uuid4().hex[:8]}"


def _call(ctx, **kwargs):
    # FakeCtx records a dict per tool; the .handler wrapper json-decodes
    # the vendor string result (host registry parity, conftest:205).
    return ctx.tools["mnemosyne_task_progress"]["handler"](**kwargs)


def test_state_without_action_infers_set(agent_factory, dsn):
    ctx = agent_factory()
    task = _name("p4facade")
    r = _call(ctx, state="pending", task=task)
    assert r["ok"] is True, r
    assert r["state"] == "pending", r
    # the write must actually be readable through the get branch
    g = _call(ctx, action="get", task=task)
    assert g["ok"] is True and g["state"] == "pending", g
    # and through list
    lst = _call(ctx, action="list")
    assert task in [t["task"] for t in lst["tasks"]], lst
    _call(ctx, action="clear", task=task)


def test_metadata_without_action_also_infers_set(agent_factory):
    ctx = agent_factory()
    task = _name("p4meta")
    r = _call(ctx, task=task,
              metadata=json.dumps({"status_note": "awaiting approval"}))
    assert r["ok"] is True, r
    _call(ctx, action="clear", task=task)


def test_get_miss_is_not_a_failure(agent_factory):
    ctx = agent_factory()
    r = _call(ctx, action="get", task=_name("never-set"))
    assert r["ok"] is True, f"get-miss must not report tool failure: {r}"
    assert r["found"] is False, r
    assert r["state"] is None, r
    assert "list" in r.get("hint", ""), r
    # bare default call on an unknown name: same shape (no action given,
    # no write fields given -> stays a read, still not a failure)
    r2 = _call(ctx, task=_name("also-never-set"))
    assert r2["ok"] is True and r2["found"] is False, r2


def test_genuine_errors_still_fail(agent_factory):
    ctx = agent_factory()
    r = _call(ctx, action="get", task="")          # list branch: ok
    assert r["ok"] is True, r
    r = _call(ctx, action="bogus", task="x")       # invalid action
    assert r["ok"] is False and "bogus" in r["error"], r
    r = _call(ctx, action="clear", task="")         # clear needs a name
    assert r["ok"] is False, r
    r = _call(ctx, action="set", task="")           # set needs a name
    assert r["ok"] is False, r


def test_metadata_json_string_stored_parsed(agent_factory, priv_schema):
    ctx = agent_factory(priv=priv_schema)
    task = _name("p4store")
    note = json.dumps({"status_note": "dual-encoded probe"})
    r = _call(ctx, action="set", state="pending", task=task, metadata=note)
    assert r["ok"] is True, r
    with _c.pgconn.connect(_c.BASE_DSN, priv_schema) as cx:
        rows = cx.query_dicts(
            "select content from working_memory where content like %s "
            "order by created_at desc limit 3",
            ("task_progress meta " + task + "%",))
    assert rows, "metadata side-note row not found"
    body = rows[0]["content"]
    # parsed-dict rendering: single-encoded ("status_note": ...) — a
    # double-encoded blob carries escaped quotes (\\") instead
    assert '"status_note": "dual-encoded probe"' in body, body
    assert '\\"' not in body, f"metadata double-encoded: {body}"
    _call(ctx, action="clear", task=task)
