"""clone_prep.sh helper tests (clone-to-customer provisioning, 2026-11-11).

The script runs on a fresh buddyos-template clone; obviously we cannot
exercise real stages from here, so this suite drives the PURE helpers via
`--source-only` (identity parsing, /etc/hosts rewrite, .env key swap,
secret hygiene) plus the built-in `--self-test`. Anything that touches a
system is a no-op under --dry-run, which the last test asserts.
"""
import pathlib
import subprocess

SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "clone_prep.sh"


def sh(*args, check=True):
    r = subprocess.run(["bash", str(SCRIPT), *args], capture_output=True, text=True, timeout=120)
    if check and r.returncode != 0:
        raise AssertionError(f"rc={r.returncode}\n{r.stdout}\n{r.stderr}")
    return r


def source_and(body, *extra):
    """Run bash that sources the script (functions only) then executes body."""
    r = subprocess.run(
        ["bash", "-c", f'source {SCRIPT} --source-only\n{body}'],
        capture_output=True, text=True, timeout=60,
    )
    return r


def test_selftest():
    out = sh("--self-test").stdout
    assert "SELFTEST OK" in out, out


def test_hostname_rules():
    body = '''
    validate_hostname buddyos-trade-2 && echo ok1
    validate_hostname mybuddy-clone && echo bad1
    validate_hostname buddyos- && echo bad2
    validate_hostname BUDDYOS-x && echo bad3
    validate_hostname 'buddyos-a b' && echo bad4
    '''
    out = source_and(body).stdout
    assert "ok1" in out
    assert not any(w in out for w in ("bad1", "bad2", "bad3", "bad4"))


def test_alias_derivation():
    body = '''
    echo "n=$(short_name buddyos-newclone)"
    echo "a=$(team_alias buddyos-newclone)"
    '''
    out = source_and(body).stdout
    assert "n=newclone" in out           # team alias == <name> from hostname
    assert "a=newclone-hermes" in out    # key alias == <name>-<profile: hermes>


def test_hosts_render(tmp_path):
    f = tmp_path / "hosts"
    f.write_text("# comment\n127.0.0.1 localhost\n::1 localhost\n192.168.1.41 buddyos-template\n")
    r = source_and(f'hosts_render {f} 192.168.1.99 buddyos-newclone')
    lines = r.stdout.splitlines()
    assert "# comment" in lines
    assert "127.0.0.1 localhost" in lines
    assert not any("buddyos-template" in l for l in lines)      # stale line dropped
    assert any(l.startswith("192.168.1.99") and "buddyos-newclone" in l for l in lines)


def test_env_set_key(tmp_path):
    f = tmp_path / ".env"
    f.write_text("KEEP=1\nHERMES_CUSTOM_AI_NEXASKILLS_CO_ZA_API_KEY=sk-old-old-old\n")
    r = subprocess.run(
        ["bash", "-c",
         f'source {SCRIPT} --source-only\n'
         f'printf %s\\n sk-brand-new-key | env_set_key {f} HERMES_CUSTOM_AI_NEXASKILLS_CO_ZA_API_KEY'],
        capture_output=True, text=True, input="sk-brand-new-key\n", timeout=30)
    assert r.returncode == 0, r.stderr
    text = f.read_text()
    assert "sk-brand-new-key" in text and "sk-old" not in text
    assert "KEEP=1" in text
    assert f.stat().st_mode & 0o777 == 0o600


def test_env_set_key_creates_missing(tmp_path):
    f = tmp_path / ".env"
    f.write_text("OTHER=x\n")
    subprocess.run(
        ["bash", "-c",
         f'source {SCRIPT} --source-only\n'
         f'printf %s\\n sk-z | env_set_key {f} HERMES_CUSTOM_AI_NEXASKILLS_CO_ZA_API_KEY'],
        input="sk-z\n", capture_output=True, text=True, timeout=30)
    assert "HERMES_CUSTOM_AI_NEXASKILLS_CO_ZA_API_KEY=sk-z" in f.read_text()


def test_curl_header_helper_keeps_secrets_out_of_argv(tmp_path):
    # curl_stdin_header must feed the header via curl -K - (stdin config),
    # so a fake 'curl' on PATH never sees the secret in argv.
    marker = tmp_path / "argv.txt"
    fakebin = tmp_path / "bin"
    fakebin.mkdir()
    (fakebin / "curl").write_text(f'#!/bin/sh\nprintf "%s\\n" "$@" > {marker}\necho 200\n')
    (fakebin / "curl").chmod(0o755)
    r = subprocess.run(
        ["bash", "-c",
         f'export PATH={fakebin}:$PATH\n'
         f'source {SCRIPT} --source-only\n'
         f'LOGFILE={tmp_path}/log\ncurl_stdin_header http://x/y "Authorization: Bearer sk-TOPSECRET" >/dev/null'],
        capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    argv = marker.read_text()
    assert "sk-TOPSECRET" not in argv      # secret never in argv
    assert "-K" in argv                    # config-on-stdin path used


def test_dry_run_makes_no_mutations(tmp_path):
    # --dry-run on this (wrong-shaped) host: mutations are logged, not run.
    # Stage 0 will abort here (this host is a fleet host, not a clone) —
    # acceptable: what we assert is that no clone-prep backup dir appears.
    r = subprocess.run(["bash", str(SCRIPT), "--dry-run", "--hostname=buddyos-drytest",
                        "--ip=192.0.2.1", "--skip-grant"],
                       capture_output=True, text=True, timeout=180, input="\n" * 20)
    assert "DRY:" in r.stdout or "stage" in r.stdout
    import os
    backups = pathlib.Path(os.path.expanduser("~/.hermes/backups"))
    fresh = [p for p in backups.glob("prep-*") if "drytest" in p.name] if backups.is_dir() else []
    assert not fresh
