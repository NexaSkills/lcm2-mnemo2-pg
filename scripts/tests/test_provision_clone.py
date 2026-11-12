"""provision_clone.sh helper tests (nexa-side orchestrator, 2026-11-12).

The orchestrator mutates the LiteLLM gateway and a live clone; obviously we
do not exercise real stages from the suite. This drives the PURE helpers
via `--source-only` (naming conventions, ipv4 validation, models JSON,
public url derivation) plus the built-in `--self-test`. The dry-run flag
is asserted to short-circuit every mutating call.
"""
import pathlib
import subprocess

SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "provision_clone.sh"


def sh(*args, check=True):
    r = subprocess.run(["bash", str(SCRIPT), *args], capture_output=True, text=True, timeout=120)
    if check and r.returncode != 0:
        raise AssertionError(f"rc={r.returncode}\n{r.stdout}\n{r.stderr}")
    return r


def source_and(body, *extra):
    r = subprocess.run(
        ["bash", "-c", f'source {SCRIPT} --source-only {" ".join(extra)}\n{body}'],
        capture_output=True, text=True, timeout=60,
    )
    return r


def test_selftest():
    out = sh("--self-test").stdout
    assert "SELFTEST OK" in out, out


def test_naming_conventions():
    r = source_and("""
    [ "$(short_name buddyos-jarvis)" = jarvis ] && echo ok1
    [ "$(user_name buddyos-jarvis)" = jarvis ] && echo ok2
    [ "$(key_alias buddyos-jarvis)" = jarvis-hermes ] && echo ok3
    """)
    assert r.stdout.count("ok") == 3, r.stdout + r.stderr


def test_hostname_rules():
    r = source_and("""
    validate_hostname buddyos-jarvis && echo ok1
    validate_hostname buddyos-big-earl && echo ok2
    validate_hostname jarvis && echo bad1
    validate_hostname buddyos- && echo bad2
    validate_hostname BUDDYOS-j && echo bad3
    """)
    assert "ok1" in r.stdout and "ok2" in r.stdout
    assert "bad" not in r.stdout


def test_dashboard_pw_derivation():
    r = source_and("""
    NEXA_PW_SUFFIX='Xx9'
    [ "$(dashboard_pw buddyos-jarvis)" = JarvisXx9 ] && echo ok1
    [ "$(dashboard_pw buddyos-big-earl)" = Big-earlXx9 ] && echo ok2
    """)
    assert "ok1" in r.stdout and "ok2" in r.stdout, r.stdout + r.stderr


def test_public_url():
    r = source_and("""
    [ "$(public_url buddyos-jarvis)" = "https://jarvis.nexaskills.co.za" ] && echo ok1
    """)
    assert "ok1" in r.stdout, r.stdout + r.stderr


def test_models_json():
    r = source_and("""
    TEAM_MODELS='main kev'
    [ "$(models_json)" = '["main","kev"]' ] && echo ok1
    TEAM_MODELS='main'
    [ "$(models_json)" = '["main"]' ] && echo ok2
    """)
    assert "ok1" in r.stdout and "ok2" in r.stdout, r.stdout + r.stderr


def test_ipv4_rules():
    r = source_and("""
    is_ipv4 192.168.1.149 && echo ok1
    is_ipv4 999.1.1.1 && echo bad1
    is_ipv4 1.2.3 && echo bad2
    """)
    assert "ok1" in r.stdout and "bad" not in r.stdout


def test_lit_api_dry_run_makes_no_calls():
    # DRY=1 must short-circuit non-GET lit_api calls (no curl, no network).
    r = source_and("""
    DRY=1
    LITELLM_MASTER_KEY=dummy
    out=$(lit_api POST /team/new '{"team_alias":"x"}' 2>/dev/null | tail -n1)
    [ "$out" = "{}" ] && echo ok1
    echo ok2
    """, )
    assert "ok1" in r.stdout and "ok2" in r.stdout, r.stdout + r.stderr


def test_usage_errors():
    r = sh("--hostname=buddyos-jarvis", check=False)  # no --ip
    assert r.returncode == 2, r.stdout + r.stderr
    r = sh("--hostname=not-valid", "--ip=1.2.3.4", check=False)
    assert r.returncode == 2, r.stdout + r.stderr
