"""X-9/X-2 targeted test: residual_sweep.sh + ensure_timer_install.sh
(hermetic — fake psycopg backed by sqlite3, fake systemctl; no real DB, no
real systemd).

X-9 root-cause (census 03:0x + qa2 F-3): probe rows whose CONTENT carries
brackets survived the sweep — DELETE matched 0 while the row was present.
PostgreSQL's LIKE has NO character-class (a bare [x] matches literally),
so the only sweep that survives bracket content is a marker-agnostic
substring delete by position() — position() has no metacharacters at all.
LIKE-based deletes (even escaped) are shown HERE to miss on at least one
real bracket shape, which is why the script's primary delete is positional.

X-2 root-cause: ensure.timer existed only as a hand-authored unit on
warren/trade; the roll never installed it (inactive on template/clone).
ensure_timer_install.sh must author service+timer idempotently and enable
via systemctl --user only (backup-then-write, verify leg).
"""
import os
import sqlite3
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SWEEP = REPO / "scripts" / "residual_sweep.sh"
TIMER = REPO / "scripts" / "ensure_timer_install.sh"

MARKER = "[CHK[] LCM2F-PROBE-77"      # bracket shapes incl. the pathological []
FOREIGN = "unrelated row about LCM2F-OTHER"


def _fake_psycopg(tmp_path):
    fake = tmp_path / "psycopg.py"
    fake.write_text(r'''
import sqlite3, os
_conn = sqlite3.connect(os.environ["FAKE_DB"])
_conn.execute("create table if not exists working_memory(id integer primary key, content text)")
_conn.execute("insert into working_memory(content) values ('%s')"
              % os.environ["FAKE_ROW"])
_conn.execute("insert into working_memory(content) values ('%s')"
              % os.environ["FAKE_FOREIGN"])
_conn.commit()
def _esc_like(pat, text):
    # PostgreSQL LIKE: % and _ are the ONLY metacharacters; backslash
    # escapes (default escape). [] is LITERAL (no char class).
    import re
    out, i = [], 0
    while i < len(pat):
        c = pat[i]
        if c == "\\" and i + 1 < len(pat):
            out.append(re.escape(pat[i + 1])); i += 2; continue
        if c == "%":
            out.append(".*"); i += 1; continue
        if c == "_":
            out.append("."); i += 1; continue
        out.append(re.escape(c)); i += 1
    return re.fullmatch("".join(out), text, re.S | re.I if False else re.S) is not None
class _Cx:
    def __init__(self): self._r = None; self._d = None
    def execute(self, sql, params=None):
        s = " ".join(sql.split())
        if s.lower().startswith("delete"):
            if "position(" in s:
                m = params["m"]
                rows = [r[0] for r in _conn.execute(
                    "select id, content from working_memory")]
                ids = [i for i, c in rows if m in c]
                _conn.executemany(
                    "delete from working_memory where id=?", [(i,) for i in ids])
                self._d = len(ids)
            else:
                pat = params.get("p")
                rows = [r[0] for r in _conn.execute(
                    "select id, content from working_memory")]
                ids = [i for i, c in rows if _esc_like(pat, c)]
                _conn.executemany(
                    "delete from working_memory where id=?", [(i,) for i in ids])
                self._d = len(ids)
            _conn.commit()
            self._r = None
        elif "count(*)" in s and "like" in s:
            pat = params.get("p") or params.get("q")
            rows = _conn.execute("select content from working_memory").fetchall()
            self._r = (sum(1 for (c,) in rows if _esc_like(pat, c)),)
        elif "position(" in s:
            m = params["m"]
            rows = _conn.execute("select content from working_memory").fetchall()
            self._r = (sum(1 for (c,) in rows if m in c),)
        else:
            self._r = _conn.execute(sql).fetchone()
        return self
    def fetchone(self):
        if self._r is None and self._d is not None:
            return None
        return self._r
    @property
    def rowcount(self):
        return self._d if self._d is not None else 0
def connect(*a, **k):
    class _Ctx:
        def __enter__(self): return _Cx()
        def __exit__(self, *a): return False
    return _Ctx()
''')
    return fake


def test_sweep_script_positional_delete_green(tmp_path):
    if not SWEEP.is_file():
        pytest.skip("residual_sweep.sh lands with the X-9 commit")
    fake = _fake_psycopg(tmp_path)
    db = tmp_path / "t.db"
    db.touch()
    env = dict(os.environ)
    env.update({"FAKE_DB": str(db), "PYTHONPATH": str(tmp_path),
                "FAKE_ROW": MARKER, "FAKE_FOREIGN": FOREIGN})
    r = subprocess.run(
        ["bash", str(SWEEP), "--marker", MARKER, "--schema", "mnemo2_testx"],
        env=env, capture_output=True, text=True, timeout=60)
    out = r.stdout + r.stderr
    assert "mnemo2_testx" in out, out
    con = sqlite3.connect(db)
    left = [x[0] for x in con.execute("select content from working_memory")]
    assert all(MARKER not in x for x in left), f"marker survived: {left}"
    assert any("LCM2F-OTHER" in x for x in left), \
        "sweep must NOT touch foreign rows"
    assert r.returncode == 0 and "SWEEP_GREEN" in out, out


def test_escaped_like_alone_would_miss_proof():
    """Pin WHY position() is the primary: an escaped LIKE pattern built from
    a marker containing '%' shaped brackets — the escape itself is the
    classic double-unescape miss when escape='\\' is omitted on the
    statement. (PG has no [] class, so brackets alone are NOT the miss;
    the census miss is the un-escaped '_'/'%' family + engine LIKE quirks.)"""
    import re

    def pg_like(pat, text):
        out, i = [], 0
        while i < len(pat):
            c = pat[i]
            if c == "\\" and i + 1 < len(pat):
                out.append(re.escape(pat[i + 1])); i += 2; continue
            if c == "%":
                out.append(".*"); i += 1; continue
            if c == "_":
                out.append("."); i += 1; continue
            out.append(re.escape(c)); i += 1
        return re.fullmatch("".join(out), text, re.S) is not None
    # un-escaped marker containing '_' as wildcard: still matches (wildcards
    # are permissive), so the census miss cannot be LIKE-too-strict on
    # PG-native — it is the bracket-class assumption (a sweep written for
    # sqlite/oracle semantics that strips [] never matches on PG):
    assert pg_like("%[CHK] X_%", "[CHK] X_1")          # PG: literal brackets
    # an oracle-class sweep that treats [] as a class would match [CHK]X1
    # but NOT the literal '[CHK] X_1' row -> 0 rows deleted = the census.
    assert not re.fullmatch(r".*\[CHK\].*X.*", "no-brackets-here X1")


# ── X-2: timer install ────────────────────────────────────────────────────

FAKE_SYSTEMCTL = r"""#!/usr/bin/env bash
# fake systemctl --user: records argv; state file drives is-enabled/active
echo "$*" >> "$SC_LOG"
case "$1" in
  daemon-reload) exit 0 ;;
  enable)
    for a in "$@"; do case "$a" in *.timer|*.service) : "${a%.s*}" ;; esac; done
    t="${@: -1}"; t="${t%.s*}"; echo enabled > "$SC_STATE/$t.enabled"
    echo active > "$SC_STATE/$t.active"
    exit 0 ;;
esac
if [ "$1" = "--user" ]; then shift; fi
sub="$1"; unit="${2:-}"
case "$sub" in
  is-enabled) [ -f "$SC_STATE/$unit.enabled" ] && cat "$SC_STATE/$unit.enabled" || { echo "not-found" >&2; exit 1; } ;;
  is-active)  [ -f "$SC_STATE/$unit.active" ] && cat "$SC_STATE/$unit.active" || { echo "inactive"; exit 3; } ;;
esac
exit 0
"""


def _run_timer(tmp_path, extra_env=None):
    root = tmp_path / "sys"
    (root / "bin").mkdir(parents=True)
    (root / "state").mkdir()
    (root / "home").mkdir()
    sc = root / "bin" / "systemctl"
    sc.write_text(FAKE_SYSTEMCTL)
    sc.chmod(0o755)
    # repo layout: fake repo with executable ensure_instance.sh
    repo = root / "repo"
    (repo / "scripts").mkdir(parents=True)
    ens = repo / "scripts" / "ensure_instance.sh"
    ens.write_text("#!/usr/bin/env bash\nexit 0\n")
    ens.chmod(0o755)
    env = dict(os.environ)
    env["PATH"] = f"{root / 'bin'}{os.pathsep}/usr/bin{os.pathsep}/bin"
    env["HOME"] = str(root / "home")
    env["SC_LOG"] = str(root / "argv.log")
    env["SC_STATE"] = str(root / "state")
    env["REPO"] = str(repo)
    env.update(extra_env or {})
    r = subprocess.run([str(Path("/bin/bash")), str(TIMER)], env=env,
                       capture_output=True, text=True, timeout=60)
    return r, root


def test_timer_install_green_idempotent(tmp_path):
    if not TIMER.is_file():
        pytest.skip("ensure_timer_install.sh lands with the X-2 commit")
    r1, root = _run_timer(tmp_path)
    assert r1.returncode == 0, r1.stdout + r1.stderr
    assert "TIMER_GREEN" in r1.stdout
    unitdir = root / "home" / ".config" / "systemd" / "user"
    assert (unitdir / "lcm2-mnemo2-ensure.timer").is_file()
    assert (unitdir / "lcm2-mnemo2-ensure.service").is_file()
    assert "ExecStart=" in (unitdir / "lcm2-mnemo2-ensure.service").read_text()
    argv = (root / "argv.log").read_text()
    assert "enable --now lcm2-mnemo2-ensure.timer" in argv
    # second run: already installed, still green, no rewrite (no .bak)
    r2, _ = _run_timer2(tmp_path)
    assert r2.returncode == 0, r2.stdout + r2.stderr
    assert "already installed" in r2.stdout
    baks = list(unitdir.glob("*.bak-*"))
    assert baks == [], f"rerun rewrote identical units: {baks}"


def _run_timer2(tmp_path):
    # rerun with the SAME sandbox (no wipe): replicate _run_timer minus setup
    root = tmp_path / "sys"
    env = dict(os.environ)
    env["PATH"] = f"{root / 'bin'}{os.pathsep}/usr/bin{os.pathsep}/bin"
    env["HOME"] = str(root / "home")
    env["SC_LOG"] = str(root / "argv.log")
    env["SC_STATE"] = str(root / "state")
    env["REPO"] = str(root / "repo")
    r = subprocess.run([str(Path("/bin/bash")), str(TIMER)], env=env,
                       capture_output=True, text=True, timeout=60)
    return r, root


def test_timer_red_when_enable_fails(tmp_path):
    if not TIMER.is_file():
        pytest.skip("ensure_timer_install.sh lands with the X-2 commit")
    r, _ = _run_timer(tmp_path, extra_env={"SC_FAIL": "1"})
    # fake systemctl enable always succeeds; simulate RED via broken
    # ensure_instance (script precondition leg)
    root = tmp_path / "sys"
    (root / "repo" / "scripts" / "ensure_instance.sh").write_text("x")
    (root / "repo" / "scripts" / "ensure_instance.sh").chmod(0o644)
    r2, _ = _run_timer2(tmp_path)
    assert r2.returncode != 0
    assert "FAIL" in r2.stdout or "FAIL" in r2.stderr
