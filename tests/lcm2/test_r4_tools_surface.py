"""R4: tool-surface parity — all 15 vendor tools registered + callable.

Live probe of the tool surface (not `hermes tools list`): register(ctx) with
a fake PluginContext, then invoke every handler against a real schema on
lcm2db:54331.
"""
import json
import os

import pytest

import pgconn
import tools as T


class FakeCtx:
    def __init__(self):
        self.tools, self.hooks, self.commands = {}, {}, {}
        self.context_engine = None
        self.skill = None

    def register_tool(self, name, handler, schema=None, **kw):
        self.tools[name] = handler

    def register_hook(self, name, handler):
        self.hooks[name] = handler

    def register_command(self, name, handler, **kw):
        self.commands[name] = name

    def register_context_engine(self, engine):
        self.context_engine = engine

    def register_skill(self, name, root, **kw):
        self.skill = name


VENDOR_TOOLS = [
    "lcm_grep", "lcm_recall", "lcm_query_state", "lcm_compute",
    "lcm_compile_evidence", "lcm_evidence_pack", "lcm_retrieve", "lcm_recent",
    "lcm_load_session", "lcm_describe", "lcm_expand", "lcm_expand_query",
    "lcm_status", "lcm_inspect", "lcm_doctor",
]


@pytest.fixture
def engine(schema):
    import engine as E
    os.environ["LCM2_DISABLE_MODEL"] = "1"  # deterministic extractive summaries
    return E.LCM2Engine(dsn=pgconn.default_dsn(), schema=schema)


def test_register_exposes_all_15_tools_and_hooks(engine):
    import importlib
    m = importlib.import_module("__init__" if False else "plugin_entry")
    ctx = FakeCtx()
    m.register(ctx, engine_factory=lambda: engine)
    assert sorted(ctx.tools) == sorted(VENDOR_TOOLS)
    assert {"pre_llm_call", "subagent_start", "subagent_stop"} <= set(ctx.hooks)
    assert ctx.context_engine is engine


@pytest.mark.parametrize("name", VENDOR_TOOLS)
def test_tool_callable_returns_json(engine, name):
    import plugin_entry
    ctx = FakeCtx()
    plugin_entry.register(ctx, engine_factory=lambda: engine)
    handler = ctx.tools[name]
    args = {"query": "test"} if name in (
        "lcm_grep", "lcm_recall") else {}
    if name == "lcm_load_session":
        args = {"session_id": "s-none"}
    if name == "lcm_recent":
        args = {"period": "today"}
    if name == "lcm_query_state":
        args = {"subject_key": "user:self"}
    if name == "lcm_compute":
        args = {"question": "count", "operands": []}
    if name == "lcm_retrieve":
        args = {"action": "status"}
    res = handler(args)
    assert isinstance(res, str)
    doc = json.loads(res)
    assert isinstance(doc, dict)
    assert "error" not in doc or doc.get("status") != "crash", doc
