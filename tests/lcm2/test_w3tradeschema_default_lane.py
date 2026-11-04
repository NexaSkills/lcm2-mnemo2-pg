"""W3TRADESCHEMA-MIKE-0030 derivation-matrix regression: the default
profile's bank must be named by PROFILE ID (lcm2_default / mnemo2_default),
never by the basename of the root home (lcm2__hermes class).

Bug (W3TRADEFIX2S-MANNY-0004 close, code-cited there): a `-p default` CLI
lane keeps HERMES_HOME pinned at the ROOT home (~/.hermes) — main.py's
early-return treats an already-pinned home as final — so the basename
derivation sanitized ".hermes" to "_hermes" and the default lane wrote
into lcm2__hermes while the gate measured lcm2_default. mnemo-pg2's
_profile_name() ALREADY mapped ".hermes" -> "default"; lcm2-pg's
agent_schema() had no such map: the two engines disagreed on the fleet's
most ordinary lane.

Fix: hermes_constants.profile_name_for_home(path) is consulted FIRST (it
returns the canonical profile id — "default" for the root home, the name
for <root>/profiles/<name>); basename-sanitize stays as the fallback for
interpreters without Hermes or exotic homes.

Convention follows data: beez AND trade already carry lcm2_default +
mnemo2_default banks created by the sanctioned cutover tooling (which
names banks by profile id), so default-lane rows landing in lcm2_default
is the one convention; lcm2__hermes is legacy residue (beez 351 rows of
turns from this bug class) awaiting migration, NOT a legitimate bank.

Matrix proved: root home (env) / -p default (root home) / -p <named>
(contextvar override) / scoped-home env / ghost scoped-home / explicit
LCM2_AGENT_SCHEMA + MNEMO2_SCHEMA pins. No gateway, no DB, no threads.
"""
import importlib.util
import os
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))          # tests/lcm2
ROOT = os.path.dirname(os.path.dirname(HERE))              # repo root

_ENV_KEYS = ("HERMES_HOME", "MNEMO2_SCHEMA", "LCM2_AGENT_SCHEMA")
_ROOT = "/home/mybuddy/.hermes"


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(
        name, os.path.join(ROOT, *rel))
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def _install_funnel(profile_exists=True, live=True):
    """Mirror the REAL hermes_constants semantics: get_hermes_home =
    contextvar override -> HERMES_HOME env -> platform default;
    profile_name_for_home maps a home path to its canonical profile id
    (root home -> 'default', <root>/profiles/<name> -> <name>)."""
    import contextvars
    override = contextvars.ContextVar("fake_home_override", default=None)
    mod = types.ModuleType("hermes_constants")

    def set_hermes_home_override(v):
        return override.set(v)

    def reset_hermes_home_override(tok):
        override.reset(tok)

    def get_hermes_home():
        v = override.get()
        if v:
            return v
        return os.environ.get("HERMES_HOME", "") or _ROOT

    def get_default_hermes_root(home=None):
        if home:
            return str(home)
        # platform default: ~/.hermes (a /profiles/<name> pin strips to root;
        # the funnel's HERMES_HOME is never treated as the root)
        return _ROOT

    def named_profile_is_live(path):
        return live

    def profile_name_for_home(path):
        if path is None or not str(path).strip():
            return None
        p = str(path).rstrip("/")
        if p == get_default_hermes_root():
            return "default"
        if os.path.basename(os.path.dirname(p)) == "profiles":
            return os.path.basename(p)
        return None

    mod.set_hermes_home_override = set_hermes_home_override
    mod.reset_hermes_home_override = reset_hermes_home_override
    mod.get_hermes_home = get_hermes_home
    mod.get_default_hermes_root = get_default_hermes_root
    mod.named_profile_is_live = named_profile_is_live
    mod.profile_name_for_home = profile_name_for_home
    prev = sys.modules.get("hermes_constants")
    sys.modules["hermes_constants"] = mod
    return prev


def test_w3tradeschema_derivation_matrix():
    for key in _ENV_KEYS:
        os.environ.pop(key, None)
    prev = _install_funnel()
    try:
        m2 = _load("w3ts_mnemo_pgconn", ("plugins", "mnemo-pg2", "pgconn.py"))
        l2 = _load("w3ts_lcm2_pgconn", ("plugins", "lcm2-pg", "pgconn.py"))
        try:
            # 1. root home via env (gateway daemon / root-on-host lane):
            #    must be the default bank, NOT lcm2__hermes.
            os.environ["HERMES_HOME"] = _ROOT
            assert l2.agent_schema() == "lcm2_default", l2.agent_schema()
            assert m2.agent_schema() == "mnemo2_default", m2.agent_schema()

            # 2. root home with NO env (platform default route): funnel
            #    default resolves to the root home -> default bank too.
            os.environ.pop("HERMES_HOME", None)
            assert l2.agent_schema() == "lcm2_default", l2.agent_schema()
            assert m2.agent_schema() == "mnemo2_default", m2.agent_schema()

            # 3. -p default CLI lane = the SAME root home via contextvar
            #    (main.py early-return keeps HERMES_HOME at the root; the
            #    '-p default' pin arrives as an override, never a re-pin):
            #    still the default bank.
            os.environ["HERMES_HOME"] = _ROOT
            tok = sys.modules["hermes_constants"].set_hermes_home_override(
                _ROOT)
            try:
                assert l2.agent_schema() == "lcm2_default", l2.agent_schema()
                assert m2.agent_schema() == "mnemo2_default", \
                    m2.agent_schema()
            finally:
                sys.modules["hermes_constants"].reset_hermes_home_override(
                    tok)

            # 4. -p <named> routed lane (contextvar override): unchanged
            #    B2 behaviour — named bank.
            for prof in ("buttercup", "brandbuddy", "debbie"):
                tok = sys.modules["hermes_constants"] \
                    .set_hermes_home_override(f"{_ROOT}/profiles/{prof}")
                try:
                    assert l2.agent_schema() == f"lcm2_{prof}"
                    assert m2.agent_schema() == f"mnemo2_{prof}"
                finally:
                    sys.modules["hermes_constants"] \
                        .reset_hermes_home_override(tok)

            # 5. scoped-home env lane: named bank (unchanged behaviour).
            os.environ["HERMES_HOME"] = f"{_ROOT}/profiles/envprof"
            assert l2.agent_schema() == "lcm2_envprof", l2.agent_schema()
            assert m2.agent_schema() == "mnemo2_envprof", m2.agent_schema()

            # 6. explicit pins still win over every derivation route.
            os.environ["LCM2_AGENT_SCHEMA"] = "lcm2_pinned"
            os.environ["MNEMO2_SCHEMA"] = "mnemo2_pinned"
            os.environ["HERMES_HOME"] = _ROOT
            assert l2.agent_schema() == "lcm2_pinned"
            assert m2.agent_schema() == "mnemo2_pinned"
            for k in ("LCM2_AGENT_SCHEMA", "MNEMO2_SCHEMA"):
                os.environ.pop(k, None)

            # 7. funnel ABSENT from the interpreter: a masked shim whose
            #    attributes raise ImportError simulates hermes_constants not
            #    existing (a plain pop re-imports the REAL Hermes here).
            #    The env-only fallback maps root '.hermes' -> 'default' —
            #    same convention as the funnel route and the mnemo side's
            #    proven map; an exotic basename still sanitizes.
            shim = types.ModuleType("hermes_constants")

            def _absent(*a, **k):
                raise ImportError("no hermes_constants on this interpreter")

            shim.get_hermes_home = _absent
            shim.profile_name_for_home = _absent
            sys.modules["hermes_constants"] = shim
            os.environ["HERMES_HOME"] = _ROOT
            assert l2.agent_schema() == "lcm2_default", l2.agent_schema()
            os.environ["HERMES_HOME"] = "/srv/weird.home"
            assert l2.agent_schema() == "lcm2_weird_home", l2.agent_schema()
            os.environ["HERMES_HOME"] = f"{_ROOT}/profiles/envprof2"
            assert l2.agent_schema() == "lcm2_envprof2"
        finally:
            for mod in ("w3ts_mnemo_pgconn", "w3ts_lcm2_pgconn"):
                sys.modules.pop(mod, None)
    finally:
        if prev is not None:
            sys.modules["hermes_constants"] = prev
        else:
            sys.modules.pop("hermes_constants", None)
        for key in _ENV_KEYS:
            os.environ.pop(key, None)
