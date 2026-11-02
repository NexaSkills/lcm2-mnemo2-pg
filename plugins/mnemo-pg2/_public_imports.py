"""Public import surface for the bare sibling names (qa3 N-3).

mnemo-pg2's modules import each other bare (`import engine`, `import
pgconn`, ...). register()'s exec window (via _SelfImportFinder) makes
those resolve to OUR files while it runs, and the host-hygiene purge at
its exit restores the PRE-EXISTING bindings byte-exactly. That is
correct for the host — but in a pytest process that ran lcm2-pg's suite
first, the pre-existing binding for `engine`/`pgconn`/`ddl`/
`plugin_entry` IS lcm2-pg's file, so a mnemo2 test module's bare
`import pgconn` silently binds lcm2-pg's routes (agent_schema() ->
lcm2_<agent>, lcm2-pg's public-busy tripwire raising on mnemo2db): the
6 ordering-coupled full-suite failures tessa filed (qa3 N-3).

Importing THIS module pins the suite scope: each bare name is resolved
through OUR file-anchored loader (by path, never the bare '_selfimport'
key — the F-1 hijack class: lcm2-pg ships a loader under that name too)
and published under the bare key ONLY when that key is unbound or
already ours — a foreign binding is never stolen (host-hygiene parity
with register()'s purge discipline; the same rule test_f1_own-
registration pins). After import, code in THIS suite resolves the bare
names to this plugin's files.
"""
import importlib.util as _ilu
import os as _os
import sys as _sys

_HERE = _os.path.dirname(_os.path.abspath(__file__))
_NAMES = ("engine", "pgconn", "ddl", "plugin_entry", "embed",
          "context_engine_shim")


def _loader():
    """OUR _selfimport copy: an already-loaded namespaced one, else a
    fresh file-anchored copy under a private key (register()'s purge
    never sees the private key; the registry cells _selfimport uses are
    located by their own keys, shared per process)."""
    for k in ("_mnemo2_impl__selfimport",):
        cand = _sys.modules.get(k)
        if cand is not None and hasattr(cand, "anchor") and _here(cand):
            return cand
    spec = _ilu.spec_from_file_location(
        "_mnemo2_public_selfimport",
        _os.path.join(_HERE, "_selfimport.py"))
    if spec is None or spec.loader is None:  # pragma: no cover
        raise ImportError("mnemo-pg2: _selfimport.py missing in " + _HERE)
    holder = _ilu.module_from_spec(spec)
    _sys.modules[spec.name] = holder
    spec.loader.exec_module(holder)
    return holder


def _here(mod):
    f = getattr(mod, "__file__", "") or ""
    return bool(f) and _os.path.realpath(
        _os.path.dirname(f)) == _os.path.realpath(_HERE)


_SI = _loader()
for _n in _NAMES:
    try:
        _mod = _SI.anchor(_n)             # OUR file, by path, always
        globals()[_n] = _mod
        _cur = _sys.modules.get(_n)
        if _cur is None or _here(_cur):   # unbound or already ours: publish
            _sys.modules[_n] = _mod
    except Exception:  # a missing optional module must not break the rest
        pass
