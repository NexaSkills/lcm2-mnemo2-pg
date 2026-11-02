"""F-1 DIAG (throwaway): who owns bare keys and pgconn.connect signature?"""
import sys, os
import pytest


def test_ownership_trace(dsn, monkeypatch):
    # simulate: lcm2 registers first (boot), then mnemo registers
    from conftest import register_plugin
    home = "/tmp/mnemo2agent_shaped_home"
    os.makedirs(home, exist_ok=True)
    monkeypatch.setenv("HERMES_HOME", home)
    monkeypatch.delenv("MNEMO2_SCHEMA", raising=False)
    monkeypatch.setenv("MNEMO2_PG_DSN", dsn)

    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "plugins", "lcm2-pg"))
    try:
        import importlib.util as ilu
        spec = ilu.spec_from_file_location(
            "lcm2_pg_init", os.path.join(os.path.dirname(__file__), "..", "..",
                                         "plugins", "lcm2-pg", "__init__.py"))
        lcm = ilu.module_from_spec(spec)
        sys.modules["hermes_plugins.lcm2_pg"] = lcm
        spec.loader.exec_module(lcm)

        class F:
            def __init__(self): self.tools={}; self.hooks={}
            def register_tool(self, name, toolset=None, schema=None, handler=None, **kw):
                self.tools[name] = dict(toolset=toolset, schema=schema, handler=handler)
            def register_hook(self, h, fn=None, **kw): self.hooks.setdefault(h, []).append(fn)
            def register_context_engine(self, eng): self.ctx_engine = eng
            def on(self, *a, **k): pass
        f1 = F()
        try:
            lcm.register(f1)
            print(f"LCM REGISTERED tools={len(f1.tools)}")
        except Exception as e:
            print(f"LCM register EXC {e!r}")
    except Exception as e:
        print(f"LCM load EXC {e!r}")

    print("AFTER-LCM bare pgconn ->", getattr(sys.modules.get("pgconn"), "__file__", None))
    print("AFTER-LCM _selfimport ->", getattr(sys.modules.get("_selfimport"), "__file__", None))

    mod, ctx = register_plugin(dsn)
    names = [n for n in ctx.tools if n.startswith("mnemosyne_")]
    print(f"MNEBO REGISTERED mnemosyne_* = {len(names)}")

    pg = sys.modules.get("pgconn")
    print("AFTER-MNEMO bare pgconn ->", getattr(pg, "__file__", None))
    try:
        import inspect
        print("connect sig ->", inspect.signature(pg.connect))
    except Exception as e:
        print("sig EXC", e)
    try:
        print("agent_schema() ->", pg.agent_schema())
    except Exception as e:
        print("agent_schema EXC", e)
    # try a tool call
    try:
        r = ctx.tools["mnemosyne_stats"]["handler"]({})
        print("stats ok:", str(r)[:200])
    except Exception as e:
        print("stats EXC", repr(e)[:300])
