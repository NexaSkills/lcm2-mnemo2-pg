"""F1 (QA phase2): the host tool surface contract.

Proves the pieces the host needs to make the 15 lcm_* tools NATIVELY
CALLABLE inside a real session, without booting the host:

  * plugin.yaml declares provides_tools (the manifest field the loader
    reads for opt-in / `hermes tools` / tool_search listings),
  * plugin_entry.register() registers every tool with a REAL input
    schema (named, parameters=object with properties) — never the old
    stub {"properties": {}},
  * the engine's get_tool_schemas() (context-engine injection path,
    agent_init._inject_context_engine_tools) returns the same named set.

End-to-end proof beyond this file lives in the result file (host-real
chat -q calls to lcm_status/lcm_grep with DB-row citations).
"""
import pathlib
import re

import pytest

REPO = pathlib.Path(__file__).resolve().parents[2]
PLUGIN_YAML = REPO / "plugins" / "lcm2-pg" / "plugin.yaml"

VENDOR_TOOLS = [
    "lcm_grep", "lcm_recall", "lcm_query_state", "lcm_compute",
    "lcm_compile_evidence", "lcm_evidence_pack", "lcm_retrieve",
    "lcm_recent", "lcm_load_session", "lcm_describe", "lcm_expand",
    "lcm_expand_query", "lcm_status", "lcm_inspect", "lcm_doctor",
]


def test_plugin_yaml_declares_provides_tools():
    text = PLUGIN_YAML.read_text()
    m = re.search(r"^provides_tools:\n((?:  - .+\n)+)", text, re.M)
    assert m, "plugin.yaml must declare a provides_tools list"
    names = [ln.strip()[2:] for ln in m.group(1).splitlines()]
    assert sorted(names) == sorted(VENDOR_TOOLS), (
        f"provides_tools mismatch: {sorted(set(VENDOR_TOOLS) ^ set(names))}")


def test_registered_schemas_are_real(registered):
    ctx, _eng = registered
    assert sorted(ctx.tools) == sorted(VENDOR_TOOLS)
    for name, fn in ctx.tools.items():
        assert callable(fn), f"{name} handler not callable"


def test_engine_schemas_named_and_object_typed():
    import schemas
    assert sorted(schemas.TOOLS) == sorted(VENDOR_TOOLS)
    for name, s in schemas.TOOLS.items():
        assert s.get("name") == name, f"{name}: schema name field mismatch"
        assert s.get("description"), f"{name}: empty description"
        params = s.get("parameters", {})
        assert params.get("type") == "object", f"{name}: params not object"
        assert params.get("properties") or params.get("additionalProperties") \
            is not True or name == "lcm_doctor", (
            f"{name}: stub schema (no properties)")


def test_manifest_version_matches_code():
    import plugin_entry
    text = PLUGIN_YAML.read_text()
    m = re.search(r"^version:\s*(\S+)\s*$", text, re.M)
    assert m, "plugin.yaml must declare version"
    assert m.group(1) == plugin_entry.__version__, (
        f"manifest {m.group(1)} != __version__ {plugin_entry.__version__}")
