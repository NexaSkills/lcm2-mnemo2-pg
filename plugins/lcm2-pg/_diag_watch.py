"""lcm2-pg F-1 diagnostic (temporary): every write/delete of the bare
'tools' key that lands OUR flat file, with the writing thread's stack.
Enabled by LCM2_DIAG=1; zero effect otherwise."""
import sys, os, threading, traceback
import os.path as _op

_HERE = _op.dirname(_op.abspath(__file__))
_started = []

def start():
    if _started or os.environ.get("LCM2_DIAG") != "1":
        return
    _started.append(1)
    target = _op.realpath(_HERE)
    log = open(os.environ.get("LCM2_DIAG_LOG", "/tmp/lcm2_diag.log"), "a", buffering=1)
    import time

    def _hooked(cls, attr, label):
        """Wrap C-API entry hooks (find_spec/find_module/load_module) on an
        import machinery class so we see EVERY call + which file it serves."""
        orig = getattr(cls, attr, None)
        if orig is None or getattr(orig, "_lcm2_diag", False):
            return

        def wrapper(*a, **k):
            name = a[1] if len(a) > 1 and isinstance(a[1], str) else (a[0] if attr == "find_module" else "?")
            try:
                r = orig(*a, **k)
            except Exception:
                raise
            if name in ("tools", "engine"):
                try:
                    org = getattr(r, "origin", None) or ""
                except Exception:
                    org = ""
                try:
                    ours = bool(org) and _op.dirname(_op.realpath(org)) == target
                except Exception:
                    ours = False
                if ours:
                    log.write(f"[{round(time.time()-t0,2)}] {label}.{attr}({name!r}) -> OUR {org}\n"
                              + "".join(traceback.format_stack())[-2200:] + "\n====\n")
            return r
        wrapper._lcm2_diag = True
        try:
            setattr(cls, attr, wrapper)
        except Exception:
            pass

    def _hook_meta():
        """Wrap find_spec on every meta_path finder installed NOW (and
        retry — finders added later get hooked by the poll loop)."""
        for f in list(sys.meta_path):
            cls = type(f)
            if getattr(cls.find_spec, "_lcm2_diag", False):
                continue
            orig = cls.find_spec

            def wrapper(self, name, path=None, target=None, _o=orig):
                try:
                    r = _o(self, name, path, target)
                except Exception:
                    log.write(f"[{round(time.time()-t0,2)}] {cls.__name__}.find_spec({name!r}) RAISED\n"
                              + "".join(traceback.format_stack())[-2200:] + "\n====\n")
                    raise
                if name and name.split(".")[0] in ("tools", "engine") and r is not None:
                    org = ""
                    ours = False
                    try:
                        org = getattr(r, "origin", "") or ""
                        ours = bool(org) and _op.dirname(_op.realpath(org)) == target
                    except Exception:
                        ours = False
                    if ours:
                        log.write(f"[{round(time.time()-t0,2)}] {cls.__name__}.find_spec({name!r}) -> OUR {org}\n"
                                  + "".join(traceback.format_stack())[-2200:] + "\n====\n")
                return r
            try:
                wrapper._lcm2_diag = True
            except Exception:
                pass
            try:
                cls.find_spec = wrapper
            except Exception:
                pass

    import importlib._bootstrap as _b
    import importlib._bootstrap_external as _be
    t0 = time.time()
    _hook_meta()
    for cls, lab in ((_b.BuiltinImporter, "BuiltinImporter"),
                     (_b.FrozenImporter, "FrozenImporter"),
                     (_be.PathFinder, "PathFinder"),
                     (_be.FileFinder, "FileFinder"),
                     (_be.FrozenImporter, "be.FrozenImporter")):
        for at in ("find_spec", "find_module", "load_module"):
            _hooked(cls, at, lab)
    try:  # the C fast path: sys._frozen_importlib is the same class set
        import sys as _s
        if getattr(_s, "_frozen_importlib", None) is not None:
            pass
    except Exception:
        pass

    def poll():
        prev = None
        mine_keys = None
        while True:
            m = sys.modules.get("tools")
            f = getattr(m, "__file__", None) if m is not None else None
            try:
                ours = bool(f) and _op.dirname(_op.realpath(f)) == target
            except Exception:
                ours = False
            st = "OURS-FLAT" if (ours and not hasattr(m, "__path__")) else "ok"
            if st == "OURS-FLAT" and prev != "OURS-FLAT":
                keys = sorted(k for k, v in list(sys.modules.items())
                              if isinstance(getattr(v, "__file__", None), str)
                              and _op.dirname(_op.realpath(v.__file__)) == target
                              and not k.startswith(("_lcm2", "lcm2_pg", "hermes_plugins", "_hermes_")))
                tn = {t.ident: t.name for t in threading.enumerate()}
                log.write(f"[{round(time.time()-t0,2)}] bare tools=OURS-FLAT current_thread={threading.current_thread().name} our_bare_keys={keys}\n")
                for tid, fr in sys._current_frames().items():
                    s = "".join(traceback.format_stack(fr))
                    if "lcm2-pg" in s or "anchor" in s:
                        log.write(f"-- {tn.get(tid, tid)}\n{s[-1800:]}\n")
                log.write("====\n")
            prev = st
            time.sleep(0.0003)
    threading.Thread(target=poll, daemon=True, name="lcm2-diag").start()
