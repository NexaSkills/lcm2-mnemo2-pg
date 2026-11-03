"""X-1 regression: deterministic context-engine slot precedence.

Boot shape (warren proof line "Plugin 'lcm-pg' registered context
engine: lcm" on a NEW-stack gateway): both lcm-pg and lcm2-pg installed
=> the ONE host slot (register_context_engine: first-wins, second
warn-and-rejected) is decided by plugin load ORDER, not by the explicit
precedence rule. engine_slot.resolve_engine_slot + enforce_slot make the
winner order-independent and make the loser log one clear line.

Deterministic proof: a fake manager reproducing the host's slot
semantics exactly — single _context_engine slot, first-wins with
rejection, ownership ledger keyed by manifest key, replacement leases
from the REAL host registration_lifecycle.ReplacementCoordinator
(handle.release bound method of lease.dispose, matching
PluginRegistration). Rival-installed is decided by an OBSERVABILITY
PROBE (what each side's own register() can see: its own plugin dirs),
not by HERMES_HOME. No threads, no real hosts.

Token LCM2-FLEET-SPRINT-1-X1
"""
import importlib.util
import itertools
import logging
import os
import socket
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))          # tests/lcm2
ROOT = os.path.dirname(os.path.dirname(HERE))              # repo root
PKG_DIR = os.path.join(ROOT, "plugins", "lcm2-pg")
_HOST_ROOT = os.path.expanduser("~/.hermes/hermes-agent")

_ENV_KEYS = ("CONTEXT_ENGINE", "LCM2_PG_DSN", "LCM2_PG_HOST",
             "LCM2_PG_PORT", "HERMES_HOME", "HOME_ISOLATION")
_ISOLATE_HOME = "/nonexistent-x1-home"

if os.path.isdir(_HOST_ROOT) and _HOST_ROOT not in sys.path:
    sys.path.append(_HOST_ROOT)
from registration_lifecycle import ReplacementCoordinator  # noqa: E402


class FakeRegistration:
    """Mirrors hermes_cli.plugins_ledger.PluginRegistration: release is
    the lease's bound dispose method (the shape _lease_for expects)."""

    def __init__(self, kind, key, plugin_key, lease):
        self.kind, self.key, self.plugin_key = kind, key, plugin_key
        self._lease = lease
        self.release = lease.dispose
        self.active = True

    def dispose(self):
        if not self.active:
            return
        self.active = False
        if self.kind == "engine_slot_defer":
            return  # pure ledger fact; holds nothing to free
        # real registration: the host's lease.restore was bound with
        # replacement=None, so dispose puts the slot back to the
        # pre-registration value (None) — exactly what a rival reclaim
        # needs: the slot frees, then the winner claims via the normal
        # host first-wins gate.
        self.release()


class FakeEngine:
    def __init__(self, name):
        self.name = name


class FakeManager:
    """Slot + ledger semantics of hermes_cli.plugins.PluginManager for
    register_context_engine: single slot, first-wins (second rejected),
    ownership ledger keyed by manifest key, coordinator leases whose
    dispose restores the slot."""

    def __init__(self):
        self._context_engine = None
        self._ownership_ledger = {}
        self.coordinator = ReplacementCoordinator()
        self.rejected = 0

    def register(self, plugin_key, name):
        if self._context_engine is not None:
            self.rejected += 1
            return None
        engine = FakeEngine(name)
        previous = self._context_engine  # host snapshots BEFORE assign
        self._context_engine = engine

        def restore(replacement):
            if self._context_engine is engine:
                self._context_engine = replacement
            return True

        lease = self.coordinator.acquire(
            ("manager_value", id(self), "_context_engine"),
            current=engine, previous=previous, restore=restore)
        handle = FakeRegistration("context_engine", name, plugin_key, lease)
        self._ownership_ledger.setdefault(plugin_key, []).append(handle)
        return handle


class FakeCtx:
    def __init__(self, manager):
        self._manager = manager


_LOG = logging.getLogger("x1-test")


def _load_engine_slot():
    spec = importlib.util.spec_from_file_location(
        "_x1_engine_slot", os.path.join(PKG_DIR, "engine_slot.py"))
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _Env:
    """Isolate every resolver input key."""

    def __enter__(self):
        self._saved = {k: os.environ.get(k) for k in _ENV_KEYS}
        for k in _ENV_KEYS:
            os.environ.pop(k, None)
        return self

    def __exit__(self, *a):
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        return False


def _name_of(mgr):
    return getattr(mgr._context_engine, "name", None)


def _run_pair(mod, mgr, side, rival_present):
    """One side's register-time enforce_slot call. The rival-installed
    probe is faked to `rival_present` (what the side's own dirs would
    show on the host); the resolver cache is bypassed via the module's
    enforce_slot __globals__ (its own resolve lookup), so each call
    re-derives against the current env."""
    ctx = FakeCtx(mgr)
    key = "lcm2-pg" if side == "lcm2-pg" else "lcm-pg"
    ename = "lcm2-pg" if side == "lcm2-pg" else "lcm"
    orig_installed = mod._rival_installed
    g = mod.enforce_slot.__globals__
    orig_resolve = g["resolve_engine_slot"]
    mod._rival_installed = lambda other: rival_present
    g["resolve_engine_slot"] = lambda s, _force=False: orig_resolve(s, _force=True)
    try:
        return mod.enforce_slot(ctx, side,
                                lambda: mgr.register(key, ename), _LOG)
    finally:
        mod._rival_installed = orig_installed
        g["resolve_engine_slot"] = orig_resolve


ORDERS = tuple(itertools.permutations(["lcm-pg", "lcm2-pg"]))


def _both(mod, env, check):
    with _Env():
        # HERMES_HOME -> nonexistent: _config_engine() reads only
        # HERMES_HOME/config.yaml first; with a missing profile config
        # it falls to the global ~/.hermes/config.yaml (on beez that
        # pins context.engine: lcm and would leak 'old' into the auto
        # tiers under test). Pointing the profile home at nothing and
        # HOME_ISOLATION off keeps the env-only signal real: it still
        # consults the global, so these tests exercise the AUTO tiers
        # only where the global config offers no engine key — force
        # isolation so they never depend on host config at all.
        os.environ["HERMES_HOME"] = _ISOLATE_HOME
        os.environ["HOME_ISOLATION"] = "1"
        os.environ.update(env)
        for order in ORDERS:
            mgr = FakeManager()
            for side in order:
                _run_pair(mod, mgr, side, rival_present=True)
            check(mgr, order)


def test_single_stack_short_circuits():
    """No rival installed => the rule is a NO-OP and the plugin's own
    claim path runs exactly as pre-X-1 (zero behavior change)."""
    mod = _load_engine_slot()
    with _Env():
        os.environ["HERMES_HOME"] = _ISOLATE_HOME
        os.environ["HOME_ISOLATION"] = "1"
        orig = mod._rival_installed
        mod._rival_installed = lambda other: False
        called = []
        try:
            assert mod.resolve_engine_slot("lcm2-pg", _force=True) == ("", "single-stack")
            mgr = FakeManager()
            tok = mod.enforce_slot(
                FakeCtx(mgr), "lcm2-pg",
                lambda: (called.append(1), mgr.register("lcm2-pg", "lcm2-pg")),
                _LOG)
            assert tok == "single-stack"
            assert called and _name_of(mgr) == "lcm2-pg"
        finally:
            mod._rival_installed = orig


def test_marker_new_wins_both_orders():
    mod = _load_engine_slot()
    def check(mgr, order):
        assert _name_of(mgr) == "lcm2-pg", f"order={order}"
    _both(mod, {"CONTEXT_ENGINE": "new"}, check)


def test_marker_old_wins_both_orders():
    mod = _load_engine_slot()
    def check(mgr, order):
        assert _name_of(mgr) == "lcm", f"order={order}"
    _both(mod, {"CONTEXT_ENGINE": "old"}, check)


def test_dsn_set_new_wins_both_orders():
    mod = _load_engine_slot()
    def check(mgr, order):
        assert _name_of(mgr) == "lcm2-pg", f"order={order}"
    _both(mod, {"LCM2_PG_DSN": "dbname=lcm2db host=/x port=1 user=u"}, check)


def test_live_probe_new_wins_both_orders():
    mod = _load_engine_slot()
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.bind(("127.0.0.1", 0))
    srv.listen(8)
    try:
        port = srv.getsockname()[1]
        def check(mgr, order):
            assert _name_of(mgr) == "lcm2-pg", f"order={order}"
        _both(mod, {"LCM2_PG_HOST": "127.0.0.1", "LCM2_PG_PORT": str(port)},
              check)
    finally:
        srv.close()


def test_dead_probe_legacy_default_wins_both_orders():
    mod = _load_engine_slot()
    def check(mgr, order):
        assert _name_of(mgr) == "lcm", f"order={order} got {_name_of(mgr)}"
    _both(mod, {"LCM2_PG_HOST": "/nonexistent-x1/probe.sock",
                "LCM2_PG_PORT": "59999"}, check)


def test_probe_socket_dir_live_wins_both_orders():
    """X-1 probe fix (C2FIX1-MIKE-2130): a Unix LCM2_PG_HOST that is a
    socket DIRECTORY with a live listener at <dir>/.s.PGSQL.<port> must
    resolve NEW. Pre-fix the probe connected to the bare dir path =>
    ECONNREFUSED against a LIVE instance => false legacy default."""
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    with tempfile.TemporaryDirectory(prefix="x1dirprobe-") as d:
        port = str(abs(hash(d)) % 10000 + 50000)
        srv.bind(os.path.join(d, f".s.PGSQL.{port}"))
        srv.listen(8)
        try:
            mod = _load_engine_slot()
            # direct probe-level assertion (not masked by _both's cache)
            with _Env():
                os.environ["LCM2_PG_HOST"] = d
                os.environ["LCM2_PG_PORT"] = port
                assert mod._instance_reachable() is True
            def check(mgr, order):
                assert _name_of(mgr) == "lcm2-pg", f"order={order}"
            _both(mod, {"LCM2_PG_HOST": d, "LCM2_PG_PORT": port}, check)
        finally:
            srv.close()


def test_probe_socket_file_still_works():
    """Regression guard for the other Unix form: host = the socket FILE
    itself (no dir form) must still resolve reachable."""
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    with tempfile.TemporaryDirectory(prefix="x1sockprobe-") as d:
        path = os.path.join(d, "pg.sock")
        srv.bind(path)
        srv.listen(8)
        try:
            mod = _load_engine_slot()
            with _Env():
                os.environ["LCM2_PG_HOST"] = path
                assert mod._instance_reachable() is True
        finally:
            srv.close()


def test_probe_dead_dir_legacy():
    """Socket dir exists but nothing listens on .s.PGSQL.<port> =>
    still the negative answer (legacy default path unchanged)."""
    mod = _load_engine_slot()
    with tempfile.TemporaryDirectory(prefix="x1deaddir-") as d:
        with _Env():
            os.environ["LCM2_PG_HOST"] = d
            os.environ["LCM2_PG_PORT"] = "59999"
            assert mod._instance_reachable() is False


def test_retire_route_fires_once():
    """The X-1 shape itself: legacy claims first, new-stack wins the
    rule => exactly one retire+reclaim, slot ends lcm2-pg.

    NOTE on the legacy-first shape: the legacy lcm-pg plugin ships NO
    engine-slot integration (its register() calls
    ctx.register_context_engine directly), so at boot the legacy side
    always claims through the plain host first-wins gate. The
    enforce-slot retire route exists for the reverse order and for
    hosts where BOTH lanes integrate. Model that boot shape faithfully:
    lcm-pg claims with a direct register (no enforce), then lcm2-pg
    arrives via enforce_slot and must retire + reclaim."""
    mod = _load_engine_slot()
    with _Env():
        os.environ["HERMES_HOME"] = _ISOLATE_HOME
        os.environ["HOME_ISOLATION"] = "1"
        os.environ["CONTEXT_ENGINE"] = "new"
        mgr = FakeManager()
        mgr.register("lcm-pg", "lcm")                       # legacy boot path (no rule)
        assert _name_of(mgr) == "lcm"
        t2 = _run_pair(mod, mgr, "lcm2-pg", rival_present=True)  # winner via enforce
        assert t2 == "slot-claimed", t2
        assert _name_of(mgr) == "lcm2-pg"
        # rival's ledger handle is retired, not orphan-active
        h = mgr._ownership_ledger["lcm-pg"][0]
        assert h.active is False


def test_legacy_enforce_defers_and_winner_claims():
    """Both-lanes-integrated shape: legacy runs enforce_slot first and
    loses the rule => it defers (slot stays FREE), new-stack arrives
    and claims through the normal gate."""
    mod = _load_engine_slot()
    with _Env():
        os.environ["HERMES_HOME"] = _ISOLATE_HOME
        os.environ["HOME_ISOLATION"] = "1"
        os.environ["CONTEXT_ENGINE"] = "new"
        mgr = FakeManager()
        t1 = _run_pair(mod, mgr, "lcm-pg", rival_present=True)
        assert t1 == "reserved-for-winner", t1
        assert _name_of(mgr) is None          # loser never touches the slot
        t2 = _run_pair(mod, mgr, "lcm2-pg", rival_present=True)
        assert t2 == "registered", t2
        assert _name_of(mgr) == "lcm2-pg"


def test_legacy_wins_rule_legacy_claims():
    """marker old + legacy enforce first: legacy claims normally, new
    yields with the ONE line (no slot churn)."""
    mod = _load_engine_slot()
    with _Env():
        os.environ["HERMES_HOME"] = _ISOLATE_HOME
        os.environ["HOME_ISOLATION"] = "1"
        os.environ["CONTEXT_ENGINE"] = "old"
        mgr = FakeManager()
        t1 = _run_pair(mod, mgr, "lcm-pg", rival_present=True)
        assert t1 == "registered" and _name_of(mgr) == "lcm"
        t2 = _run_pair(mod, mgr, "lcm2-pg", rival_present=True)
        assert t2 == "yielded", t2            # rival occupies slot, we lose
        assert _name_of(mgr) == "lcm"
