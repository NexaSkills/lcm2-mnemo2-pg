"""C2RESID2S: hermetic (no-fleet) tests for the C2RESID2S legs.

Legs covered:
  1. engine_slot._config_engine ruamel/yaml-FREE read (regex text fallback)
     — THE G1 blocker class: the gateway tools-exe carries neither PyYAML
     nor ruamel, and hermes_cli.config dies at `import hermes_yaml`.
  2. root-surface hardening: cutover_capture_dirs.sh authors the ROOT
     launch-scope capture dir; onboard_profile.sh authors the launch-scope
     schema (InvalidSchemaName class).
  3. env_depcheck.sh ruamel-presence leg (fail-closed, names the cure).
  4. capture-dir schema-ensure leg (fail-soft, name-validated).
"""
import os
import shutil
import subprocess
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
CAP = ROOT / "plugins/context_engine/lcm2-pg"
ONBOARD = ROOT / "scripts/onboard_profile.sh"
CUTOVER = ROOT / "scripts/cutover_capture_dirs.sh"
DEPCHECK = ROOT / "scripts/env_depcheck.sh"


def _load(name, path, pkg_dirs=None):
    import importlib.util as ilu
    spec = ilu.spec_from_file_location(
        name, path, submodule_search_locations=pkg_dirs)
    assert spec is not None and spec.loader is not None
    m = ilu.module_from_spec(spec)
    sys.modules[name] = m
    spec.loader.exec_module(m)
    return m


class _BlockYaml:
    """Context manager: `import yaml`/`hermes_yaml`/`ruamel` raise ImportError
    (the gateway tools-exe reality), everything else imports normally."""

    def __enter__(self):
        import builtins
        self.real = builtins.__import__

        def fake(name, *a, **k):
            if name in ("yaml", "hermes_yaml", "ruamel", "ruamel.yaml"):
                raise ImportError("blocked for test (gateway tools-exe)")
            return self.real(name, *a, **k)
        builtins.__import__ = fake
        return self

    def __exit__(self, *a):
        import builtins
        builtins.__import__ = self.real
        return False


# ---- leg 1: ruamel-free config read (THE G1 blocker) --------------------

def test_config_engine_no_yaml_module(tmp_path, monkeypatch):
    cases = {
        "block": ("context:\n  engine: lcm2-pg\n", "new"),
        "flow": ("context: {engine: lcm2-pg, memory_trim: {enabled: true}}\n",
                 "new"),
        "quoted": ("context:\n  engine: 'LCM2-PG'\n", "new"),
        "comment": ("context:\n  # note\n  engine: lcm2-pg  # inline\n",
                    "new"),
        "other-first": ("plugins:\n  isolation: host\ncontext:\n  engine: lcm\n",
                        "old"),
        "sub-block-first": ("context:\n  memory_trim:\n    enabled: true\n  engine: lcm2-pg\n",
                            "new"),
        "no-engine": ("context:\n  memory_trim:\n    enabled: true\n", ""),
        "empty": ("", ""),
        "unrelated": ("model:\n  default: main\n", ""),
    }
    for i, (block, (body, want)) in enumerate(cases.items()):
        home = tmp_path / block
        home.mkdir()
        (home / "config.yaml").write_text(body, encoding="utf-8")
        monkeypatch.delenv("HOME_ISOLATION", raising=False)
        # HOME pinned to an empty dir: the fallback leg (~/.hermes) must
        # never see the real host home (would leak a root `context:
        # engine: lcm2-pg` into a per-home test).
        fakehome = tmp_path / f"home{i}"
        fakehome.mkdir()
        monkeypatch.setenv("HOME", str(fakehome))
        monkeypatch.setenv("HERMES_HOME", str(home))
        name = f"slot_g1_{i}"
        with _BlockYaml():
            m = _load(name, ROOT / "plugins/lcm2-pg/engine_slot.py")
            got = m._config_engine()
        sys.modules.pop(name, None)
        assert got == want, (block, got, want)


def test_config_engine_text_unit(tmp_path):
    m = _load("slot_u", ROOT / "plugins/lcm2-pg/engine_slot.py")
    f = tmp_path / "config.yaml"
    try:
        f.write_text("context:\n  engine: lcm2-pg\nmemory:\n  engine: WRONG\n",
                     encoding="utf-8")
        assert m._config_engine_text(str(f)) == "lcm2-pg"
        f.write_text("context: {engine: lcm}\n", encoding="utf-8")
        assert m._config_engine_text(str(f)) == "lcm"
        f.write_text("plugins:\n  x: 1\n", encoding="utf-8")
        assert m._config_engine_text(str(f)) == ""
        # a nested `engine:` OUTSIDE the context block must never leak in
        f.write_text("memory:\n  engine: not-an-engine\n", encoding="utf-8")
        assert m._config_engine_text(str(f)) == ""
    finally:
        sys.modules.pop("slot_u", None)


def test_config_engine_degrades_quietly(tmp_path, monkeypatch):
    m = _load("slot_d", ROOT / "plugins/lcm2-pg/engine_slot.py")
    try:
        fakehome = tmp_path / "home"
        fakehome.mkdir()
        monkeypatch.setenv("HOME", str(fakehome))
        monkeypatch.delenv("HOME_ISOLATION", raising=False)
        monkeypatch.setenv("HERMES_HOME", str(tmp_path / "missing"))
        assert m._config_engine() == ""  # never raises
        with _BlockYaml():
            assert m._config_engine() == ""
    finally:
        sys.modules.pop("slot_d", None)


# ---- leg 2: root-surface (capture dir + launch schema) ------------------

def test_cutover_ships_root_capture_leg():
    src = CUTOVER.read_text(encoding="utf-8")
    assert 'root_link="$HERMES_BASE/plugins/context_engine/lcm2-pg"' in src
    assert "skip root" in src and "set  root" in src


def test_cutover_root_leg_dry_run(tmp_path):
    home = tmp_path / "hb"
    (home / "plugins").mkdir(parents=True)
    binp = tmp_path / "bin"
    binp.mkdir()
    fake = binp / "hermes"
    fake.write_text('#!/bin/sh\n[ "$1" = "-p" ] && exit 0\necho lcm2-pg\n',
                    encoding="utf-8")
    fake.chmod(0o755)
    env = dict(os.environ, PATH=f"{binp}:{os.environ['PATH']}",
               HERMES_BASE=str(home))
    r = subprocess.run(["bash", str(CUTOVER), "--dry-run"], env=env,
                       capture_output=True, text=True, timeout=60)
    assert "root" in r.stdout, r.stdout
    assert "DRY: ln -sfn" in r.stdout or "ok   root" in r.stdout, r.stdout


def test_onboard_ships_root_schema_leg():
    src = ONBOARD.read_text(encoding="utf-8")
    # W3TKT-MIKE-1220 #3 landed the quoting cure: the identifier is now
    # the quoted form \"$_root_schema\" (a bare hyphen-dirty token is a
    # SQL syntax error — same leg, quoted emission).
    assert 'CREATE SCHEMA IF NOT EXISTS \\"$_root_schema\\"' in src \
        or "CREATE SCHEMA IF NOT EXISTS $_root_schema" in src  # either form
    assert "InvalidSchemaName" in src  # names the class it cures
    assert "degenerate" in src  # never builds an illegal schema name


# ---- leg 3: env_depcheck ruamel leg (fail-closed) -----------------------

def test_env_depcheck_ships_ruamel_leg():
    src = DEPCHECK.read_text(encoding="utf-8")
    assert '"ruamel"' in src and '"yaml"' in src  # probe surface widened
    assert "hermes_yaml" in src  # names the module-scope import chain


# ---- leg 4: capture-dir schema-ensure (fail-soft, validated) -----------

def test_capture_dir_ships_ensure_leg():
    src = (CAP / "__init__.py").read_text(encoding="utf-8")
    assert "CREATE SCHEMA IF NOT EXISTS" in src
    assert "_ensure_schema(pkg)" in src
    assert "ContextEngine" in src  # loader heuristic marker intact


def _cap_module(monkeypatch, tmp_path, fake_pgconn, preload_pgconn=True):
    """Load the capture __init__ with pgconn resolution controlled:
    fake_pgconn preloaded under the namespaced key (preload_pgconn=True) or
    absent (False — then the file-candidate list decides; point it at the
    real repo dir via preload-path so the leg stays deterministic)."""
    import psycopg

    class _Cx:
        sqls = []
        dsns = []

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def execute(self, sql, params=None):
            _Cx.sqls.append(sql)

    def fake_connect(dsn, **k):
        _Cx.dsns.append(dsn)
        return _Cx()
    monkeypatch.setattr(psycopg, "connect", fake_connect)
    monkeypatch.setenv("LCM2_PLUGIN_DIR", str(tmp_path))
    saved = dict(sys.modules)
    if preload_pgconn:
        sys.modules["_lcm2_pg_ce_pgconn"] = fake_pgconn

    def cleanup():
        for k in set(sys.modules) - set(saved):
            sys.modules.pop(k, None)
        sys.modules.pop("cap_t", None)
    mod = _load("cap_t", CAP / "__init__.py", pkg_dirs=[str(CAP)])
    return mod, _Cx, cleanup


def test_ensure_schema_validates_name(monkeypatch, tmp_path):
    fake = types.ModuleType("_lcm2_pg_ce_pgconn")
    setattr(fake, "default_dsn", lambda: "dsn-nowhere")
    setattr(fake, "agent_schema", lambda: "bad; DROP SCHEMA x")
    mod, Cx, cleanup = _cap_module(monkeypatch, tmp_path, fake)
    try:
        mod._ensure_schema(None)
        assert Cx.dsns == []  # invalid name never reaches connect
    finally:
        cleanup()


def test_ensure_schema_fail_soft(tmp_path, monkeypatch):
    """No pgconn reachable at ALL (namespaced key absent + every candidate
    dir empty): never raises, zero connects (pre-leg behavior stands).
    NOTE: candidates include Path(__file__)-anchored repo siblings, so a
    fake HOME alone is NOT enough — the sibling dirs are moved aside
    first and restored afterwards (a destructive test must leave the
    repo exactly as it found it)."""
    import psycopg
    calls = []
    monkeypatch.setattr(psycopg, "connect",
                        lambda *a, **k: calls.append(a))
    empty = tmp_path / "empty"
    empty.mkdir()
    # kill the file-anchored candidates: the __file__-sibling
    # (repo plugins/lcm2-pg — moved aside, restored in finally), the two
    # ~ literals (HOME now resolves to an empty fake home), and any
    # namespaced key left by an earlier test in this process.
    sibling = CAP.resolve().parent.parent / "lcm2-pg"  # repo plugins/lcm2-pg
    bak = tmp_path / "sibling-bak"
    moved = False
    if sibling.is_dir():
        shutil.move(str(sibling), str(bak))  # cross-FS safe: repo and
        # /tmp are separate mounts on the fleet hosts (single-FS on the
        # reference box masked this — 2026-11-09 host-suite hunt)
        moved = True
    home = tmp_path / "fakehome"
    (home / ".hermes" / "plugins" / "lcm2-pg").mkdir(parents=True)
    (home / "lcm2-mnemo2-pg").mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("LCM2_PLUGIN_DIR", str(empty))
    saved = dict(sys.modules)
    sys.modules.pop("_lcm2_pg_ce_pgconn", None)
    try:
        mod = _load("cap_f", CAP / "__init__.py", pkg_dirs=[str(CAP)])
        try:
            mod._ensure_schema(None)
            assert calls == []
        finally:
            for k in set(sys.modules) - set(saved):
                sys.modules.pop(k, None)
            sys.modules.pop("cap_f", None)
    finally:
        if moved and bak.is_dir() and not sibling.exists():
            shutil.move(str(bak), str(sibling))  # restore the repo layout exactly


def test_ensure_schema_runs_the_leg(monkeypatch, tmp_path):
    fake = types.ModuleType("_lcm2_pg_ce_pgconn")
    setattr(fake, "default_dsn", lambda: "dsn-recorded")
    setattr(fake, "agent_schema", lambda: "lcm2_legtest")
    mod, Cx, cleanup = _cap_module(monkeypatch, tmp_path, fake)
    try:
        mod._ensure_schema(None)
    finally:
        cleanup()
    assert Cx.dsns == ["dsn-recorded"]
    assert Cx.sqls == ['CREATE SCHEMA IF NOT EXISTS "lcm2_legtest"']
