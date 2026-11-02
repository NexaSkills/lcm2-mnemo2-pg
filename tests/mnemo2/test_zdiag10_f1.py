"""F-1 DIAG10: run r7-shaped test AFTER lcm2 register; dump failure reason."""
import sys, os, uuid


def test_zdiag10(dsn, tmp_path, request):
    import importlib.util as ilu
    root = os.path.join(os.path.dirname(__file__), "..", "..", "plugins")

    class F:
        def __init__(self): self.tools={}; self.hooks={}
        def register_tool(self, name, toolset=None, schema=None, handler=None, **kw):
            self.tools[name] = dict(toolset=toolset, schema=schema, handler=handler)
        def register_hook(self, h, fn=None, **kw): self.hooks.setdefault(h, []).append(fn)
        def register_context_engine(self, eng): self.ctx_engine = eng
        def on(self, *a, **k): pass

    spec = ilu.spec_from_file_location(
        "hermes_plugins.lcm2_pg", os.path.join(root, "lcm2-pg", "__init__.py"))
    lcm = ilu.module_from_spec(spec)
    sys.modules["hermes_plugins.lcm2_pg"] = lcm
    spec.loader.exec_module(lcm)
    f1 = F()
    lcm.register(f1)
    print(f"LCM tools={len(f1.tools)}")

    # now the exact r7 sequence
    home = tmp_path / "mnemo2agent"
    home.mkdir()
    keep = dict(os.environ)
    def restore():
        os.environ.clear(); os.environ.update(keep)
        os.environ["MNEMO2_PG_DSN"] = dsn
    request.addfinalizer(restore)
    os.environ["HERMES_HOME"] = str(home)
    os.environ.pop("MNEMO2_SCHEMA", None)

    import pgconn
    print("BARE pgconn ->", pgconn.__file__)
    print("agent_schema ->", pgconn.agent_schema())
    print("envbind ctx? ->", getattr(pgconn, "agent_schema", None) is not None)

    from conftest import register_plugin
    mod, ctx = register_plugin(dsn)
    print("after-reg bare pgconn ->", sys.modules["pgconn"].__file__)
    print("after-reg agent_schema ->", sys.modules["pgconn"].agent_schema())
    marker = "liveops-" + uuid.uuid4().hex[:8]
    r = ctx.tools["mnemosyne_remember"]["handler"](
        {"content": f"live row {marker}", "scope": "global", "importance": 0.8})
    print("remember ->", str(r)[:200])
