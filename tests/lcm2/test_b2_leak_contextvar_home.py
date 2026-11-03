"""B2 leak-fix regression: per-profile schema derivation must follow the
LIVE profile home through the host's contextvar-aware funnel
(hermes_constants.get_hermes_home), NOT a raw os.environ HERMES_HOME read.

Bug (b1-leak-probe.md): a multiplexed gateway scopes each routed turn with
a CONTEXTVAR (gateway/run.py _profile_runtime_scope ->
set_hermes_home_override) and deliberately never mutates os.environ, so
raw-env readers derived mnemo2_default / lcm2_default for every routed
profile (fleet-wide write-collapse).

Proof shape: stdlib contextvars mirror of the host override semantics
(set -> get_hermes_home sees the routed home; reset -> back to launch
root). Plus: explicit bind layers (MNEMO2_SCHEMA / LCM2_AGENT_SCHEMA /
bound HERMES_HOME) still win over the funnel, and with no host funnel the
env behaviour is unchanged. No gateway, no DB, no threads.

Token B2-LEAKFIX-MIKE-1722
"""
import contextvars
import importlib.util
import os
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))          # tests/lcm2
ROOT = os.path.dirname(os.path.dirname(HERE))              # repo root

_ENV_KEYS = ("HERMES_HOME", "MNEMO2_SCHEMA", "LCM2_AGENT_SCHEMA")


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(
        name, os.path.join(ROOT, *rel))
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def _install_fake_funnel():
    """Mirror hermes_constants contextvar semantics: a context-local
    override consulted by get_hermes_home(), env only as fallback."""
    override = contextvars.ContextVar("fake_hermes_home_override",
                                       default=None)
    mod = types.ModuleType("hermes_constants")

    def set_hermes_home_override(v):
        return override.set(v)

    def reset_hermes_home_override(tok):
        override.reset(tok)

    def get_hermes_home():
        v = override.get()
        return v if v else os.environ.get("HERMES_HOME", "")

    mod.set_hermes_home_override = set_hermes_home_override
    mod.reset_hermes_home_override = reset_hermes_home_override
    mod.get_hermes_home = get_hermes_home
    prev = sys.modules.get("hermes_constants")
    sys.modules["hermes_constants"] = mod
    return prev


def _cleanup(prev):
    if prev is not None:
        sys.modules["hermes_constants"] = prev
    else:
        sys.modules.pop("hermes_constants", None)


def test_b2_schema_follows_contextvar_home():
    for key in _ENV_KEYS:
        os.environ.pop(key, None)
    prev = _install_fake_funnel()
    try:
        m2 = _load("b2_mnemo_pgconn", ("plugins", "mnemo-pg2", "pgconn.py"))
        l2 = _load("b2_lcm2_pgconn", ("plugins", "lcm2-pg", "pgconn.py"))
        try:
            # routed turn scoping: profile home via CONTEXTVAR only
            for prof in ("buttercup", "brandbuddy", "debbie"):
                home = f"/home/mybuddy/.hermes/profiles/{prof}"
                tok = sys.modules["hermes_constants"] \
                    .set_hermes_home_override(home)
                try:
                    assert m2._profile_name() == prof, \
                        f"mnemo2 leak: {m2._profile_name()} != {prof}"
                    assert l2.agent_schema() == f"lcm2_{prof}", \
                        f"lcm2 leak: {l2.agent_schema()} != lcm2_{prof}"
                finally:
                    sys.modules["hermes_constants"] \
                        .reset_hermes_home_override(tok)
            # after scope reset, falls back to env (unbound -> default)
            assert m2._profile_name() == "default"
            assert l2.agent_schema() == "lcm2_default"

            # explicit bind layer still wins over the funnel
            os.environ["MNEMO2_SCHEMA"] = "mnemo2_pinned"
            os.environ["LCM2_AGENT_SCHEMA"] = "lcm2_pinned"
            tok = sys.modules["hermes_constants"].set_hermes_home_override(
                "/home/mybuddy/.hermes/profiles/other")
            try:
                assert m2.agent_schema() == "mnemo2_pinned"
                assert l2.agent_schema() == "lcm2_pinned"
            finally:
                sys.modules["hermes_constants"] \
                    .reset_hermes_home_override(tok)
                os.environ.pop("MNEMO2_SCHEMA", None)
                os.environ.pop("LCM2_AGENT_SCHEMA", None)

            # no funnel path: env HERMES_HOME behaviour unchanged
            del sys.modules["hermes_constants"]
            os.environ["HERMES_HOME"] = "/home/mybuddy/.hermes/profiles/envprof"
            assert m2._profile_name() == "envprof"
            assert l2.agent_schema() == "lcm2_envprof"
        finally:
            for mod in ("b2_mnemo_pgconn", "b2_lcm2_pgconn"):
                sys.modules.pop(mod, None)
    finally:
        _cleanup(prev)
        for key in _ENV_KEYS:
            os.environ.pop(key, None)
