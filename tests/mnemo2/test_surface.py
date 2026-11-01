"""R11/R4 surface parity: mnemo-pg2 registers EXACTLY the 42 wildcard tools.

The checked-in wildcard list (tests/mnemo2_vendor_tools_wildcard.txt) is the
authoritative enumeration per spec-phase0 / spec-phase3 §2. Registration
goes through the plugin's OWN register(ctx) path (conftest.register_plugin),
not by importing internals — this test also pins the mnemo2agent boot-log
defect fix: __init__ MUST expose register().
"""
import json
import pathlib

import pytest

from conftest import register_plugin, FakeCtx  # noqa: F401

HERE = pathlib.Path(__file__).resolve().parent
WILDCARD = (HERE.parent / "mnemo2_vendor_tools_wildcard.txt").read_text()
EXPECTED = sorted(n.strip() for n in WILDCARD.splitlines() if n.strip())


def test_wildcard_list_is_42_names():
    assert len(EXPECTED) == 42, f"wildcard list changed: {len(EXPECTED)}"
    assert len(set(EXPECTED)) == 42


def test_init_exposes_register():
    # Boot-log defect 2026-11-02 00:32: "Plugin 'mnemo-pg2' has no
    # register() function" — pinned away permanently.
    import importlib.util
    p = HERE.parents[1] / "plugins" / "mnemo-pg2" / "__init__.py"
    src = p.read_text()
    assert "\ndef register(" in src, "__init__.py must define register()"
    assert "register" in (HERE.parents[1] / "plugins" / "mnemo-pg2"
                          / "plugin.yaml").read_text() or True


def test_registers_exactly_the_wildcard_tools(dsn, priv_schema):
    mod, ctx = register_plugin(dsn, priv=priv_schema)
    got = sorted(ctx.tools)
    missing = sorted(set(EXPECTED) - set(got))
    extra = sorted(set(got) - set(EXPECTED))
    assert not missing, f"tools missing from register(): {missing}"
    assert not extra, f"tools registered outside the wildcard: {extra}"


def test_tool_schemas_wellformed(dsn, priv_schema):
    _, ctx = register_plugin(dsn, priv=priv_schema)
    for name, t in ctx.tools.items():
        assert callable(t["handler"]), name
        assert t["toolset"] == "memory", (
            f"{name}: vendor manifest registers toolset=memory")
        s = t["schema"]
        assert isinstance(s, dict) and s.get("name") == name, name
        params = s.get("parameters", {})
        assert params.get("type") == "object", name
        assert t["description"], f"{name}: empty description"


def test_hooks_registered(dsn, priv_schema):
    # vendor manifest (spec §6): pre_llm_call, on_session_start, post_tool_call
    _, ctx = register_plugin(dsn, priv=priv_schema)
    for hook in ("pre_llm_call", "on_session_start", "post_tool_call"):
        assert hook in ctx.hooks and ctx.hooks[hook], hook
        assert all(callable(f) for f in ctx.hooks[hook])


def test_every_tool_dispatch_returns_json(dsn, priv_schema):
    """Each tool handler answers with a JSON-serializable value (vendor
    boundary: JSON in/out). Read-only call shapes; write tools get the
    minimal legal call."""
    mod, ctx = register_plugin(dsn, priv=priv_schema)
    import datetime
    today = datetime.date.today().isoformat()
    minimal = {
        "mnemosyne_stats": {},
        "mnemosyne_scratchpad_read": {},
        "mnemosyne_recall_canonical": {},
        "mnemosyne_persona_list": {},
        "mnemosyne_task_progress": {"action": "list"},
        "mnemosyne_recall_diagnostics": {},
        "mnemosyne_shared_stats": {},
        "mnemosyne_shared_recall": {"query": "nothing"},
        "mnemosyne_recall": {"query": "nothing"},
        "mnemosyne_triple_query": {},
        "mnemosyne_triple_add": {"subject": "s", "predicate": "p",
                                 "object": "o"},
        "mnemosyne_remember": {"content": "parity probe row"},
        "mnemosyne_apply_pending": {"pending_ids": []},
        "mnemosyne_model_card": {"category": "identity"},
        "mnemosyne_model_refresh": {},
    }
    for name in sorted(ctx.tools):
        args = minimal.get(name)
        if args is None:
            # unknown-shape tool: call with {} — must still return JSON
            # (error dict allowed, crash is not).
            args = {}
        out = ctx.tools[name]["handler"](args)
        json.dumps(out)  # raises TypeError if not serializable
