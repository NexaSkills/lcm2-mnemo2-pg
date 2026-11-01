"""R4: tool-surface parity — all 15 vendor tools registered + callable.

Live probe of the tool surface (not `hermes tools list`): register(ctx) with
a fake PluginContext, then invoke every handler against a real schema on
lcm2db:54331.
"""
import importlib
import json

import pytest

import pgconn


class FakeCtx:
    def __init__(self):
        self.tools, self.hooks, self.commands = {}, {}, {}
        self.context_engine = None
        self.skill = None

    def register_tool(self, name=None, handler=None, schema=None, **kw):
        self.tools[name] = handler

    def register_hook(self, name, handler):
        self.hooks[name] = handler

    def register_command(self, name=None, handler=None, **kw):
        self.commands[name] = handler

    def register_context_engine(self, engine):
        self.context_engine = engine

    def register_skill(self, name, root=None, **kw):
        self.skill = name


VENDOR_TOOLS = [
    "lcm_grep", "lcm_recall", "lcm_query_state", "lcm_compute",
    "lcm_compile_evidence", "lcm_evidence_pack", "lcm_retrieve", "lcm_recent",
    "lcm_load_session", "lcm_describe", "lcm_expand", "lcm_expand_query",
    "lcm_status", "lcm_inspect", "lcm_doctor",
]


@pytest.fixture
def registered(dsn, schema):
    import os
    os.environ["LCM2_DISABLE_MODEL"] = "1"  # deterministic extractive summaries
    import engine as E
    eng = E.LCM2Engine(dsn=dsn, schema=schema)
    entry = importlib.import_module("plugin_entry")
    ctx = FakeCtx()
    # one FakeCtx per test; the registered() fixture is function-scoped, so
    # sibling tests in the same run never race on its dicts
    entry.register(ctx, engine_factory=lambda: eng)
    return ctx, eng


def test_register_exposes_all_15_tools_and_hooks(registered):
    ctx, eng = registered
    assert sorted(ctx.tools) == sorted(VENDOR_TOOLS)
    assert {"pre_llm_call", "subagent_start", "subagent_stop"} <= set(ctx.hooks)
    assert ctx.context_engine is eng


@pytest.mark.parametrize("name", VENDOR_TOOLS)
def test_tool_callable_returns_json(registered, name):
    ctx, eng = registered
    handler = ctx.tools[name]
    args = {}
    if name in ("lcm_grep", "lcm_recall"):
        args = {"query": "test"}
    elif name == "lcm_load_session":
        args = {"session_id": "s-none"}
    elif name == "lcm_recent":
        args = {"period": "today"}
    elif name == "lcm_query_state":
        args = {"subject_key": "user:self"}
    elif name == "lcm_compute":
        args = {"question": "count", "operands": []}
    elif name == "lcm_retrieve":
        args = {"action": "status"}
    res = handler(args)
    assert isinstance(res, str)
    doc = json.loads(res)
    assert isinstance(doc, dict)
    assert doc.get("implementation") == "lcm2-pg/native"
