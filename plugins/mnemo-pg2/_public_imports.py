"""Public surface (qa3 N-3): bare `import pgconn` / `import ddl` /
`import engine` / `import plugin_entry` from THIS dir binds THIS plugin's
files — never the rival plugin's. lcm2-pg ships the same bare module
names; whichever loader ran last owns the sys.modules key, so a mnemo2
test whose `import pgconn` lands after lcm2's tests ran silently hit
lcm2-pg's routes (agent_schema() -> lcm2_mnemo2agent, and lcm2-pg's
public-busy tripwire raised on mnemo2db — the 6 ordering-coupled
full-suite failures). Resolution is BY FILE through our own namespaced
loader (_selfimport.anchor); no sys.path games, no key theft.
"""
import importlib.util as _ilu
import os as _os
import sys as _sys

_HERE = _os.path.dirname(_os.path.abspath(__file__))
_NAMES = ("pgconn", "ddl", "engine", "plugin_entry")


def _same_here(mod):
    f = getattr(mod, "__file__", "") or ""
    return bool(f) and _os.path.dirname(_os.path.abspath(f)) == _HERE


def _install(name):
    # Already bound to OUR file (by register() or an earlier import)?
    for key in (name, f"_mnemo2_impl_{name}"):
        cur = _sys.modules.get(key)
        if cur is not None and _same_here(cur):
            globals()[name] = cur
            _sys.modules[name] = cur
            return
    # Load BY FILE through our own loader. _selfimport lives in THIS dir
    # and keys its work _mnemo2_impl_* / <pkg>.* namespaced (the bare-key
    # hijack class pinned by test_f1_own_registration), so the bare key
    # only lands on OUR file.
    spec = _ilu.spec_from_file_location(
        "_mnemo2_public_selfimport", _os.path.join(_HERE, "_selfimport.py"))
    if spec is None or spec.loader is None:  # pragma: no cover
        return
    holder = _ilu.module_from_spec(spec)
    _sys.modules[spec.name] = holder
    try:
        spec.loader.exec_module(holder)
        mod = holder.anchor(name)
    finally:
        _sys.modules.pop(spec.name, None)
    cur = _sys.modules.get(name)
    if cur is not None and not _same_here(cur):
        return  # a foreign file owns the bare key: never steal it in-suite
    globals()[name] = mod
    _sys.modules[name] = mod


for _n in _NAMES:
    try:
        _install(_n)
    except Exception:  # one missing module must not break the others
        pass
