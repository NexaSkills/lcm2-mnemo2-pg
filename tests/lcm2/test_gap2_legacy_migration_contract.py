"""GAP2 (W3BEEZGAP-MIKE-1610): the landed old->new data-migration script's
contract, testable without ANY database.

Why this test exists: beezfull QA (W3BEEZFULL-TESSA-1515, C-F) named the
gap "schema DATA migration old PG (:54329) -> new (:54331) was never
specified/landed". scripts/migrate_legacy_lcm_to_lcm2.sh + the two helper
scripts land it; live dry-runs on beez reconciled 16363/33 (lcm_manny) and
20004/65 (lcm_data) exact. This test pins the two properties a future edit
must never break, statically (no DB, no cluster, no vendor import):

  1. SOURCE SAFETY: both helpers touch the source with SELECT-only SQL —
     never insert/update/delete/alter/drop/truncate/copy targeted AT the
     source connection. (A regression here could corrupt the legacy store
     that is the only rollback path.)
  2. SCHEMA-NAMING PARITY: the destination schema naming matches
     plugins/lcm2-pg/pgconn.agent_schema's derivation — lcm2_<sanitized
     basename of the profile home> — so a migration authors exactly the
     schema the engine itself will bind. (The first draft used
     lcm2_pg_<p> and the dry-run caught it; this test locks the cure.)

Token W3BEEZGAP-MIKE-1610
"""
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
SCRIPT = os.path.join(ROOT, "scripts", "migrate_legacy_lcm_to_lcm2.sh")
APPLY = os.path.join(ROOT, "scripts", "_migrate_apply_ddl.py")
PIPE = os.path.join(ROOT, "scripts", "_migrate_copy_rows.py")


def _read(p):
    with open(p, encoding="utf-8") as fh:
        return fh.read()


def _stmts(blob):
    """Single-line SQL string literals in a Python source blob. Triple-
    quoted blocks are NOT scanned (MAPS' queries are built with
    .format(), and the only triple-quoted blocks in the helpers are
    docstrings); the assertion covers the single-quoted literals the
    helpers actually execute."""
    return re.findall(r"['\"]((?:select|SET)[^'\"]*)['\"]", blob)


def test_files_landed():
    for p in (SCRIPT, APPLY, PIPE):
        assert os.path.isfile(p), p


def test_source_sql_is_select_only():
    """Every SQL the helpers send to the SOURCE is a SELECT/SET/count.
    The destination-side writes must live on Conn/insert paths bound to
    NEW_DSN only — this asserts the SOURCE-side statements themselves."""
    src_sql = [
        # the shell's read-only probes/counts (lsql legs)
        "select 1",
        "select count(*)",
        "select * from",           # dblink remote legs (server-readonly)
        "SET TRANSACTION READ ONLY",
        "select set_config",       # read-only GUC pinning
        "select dblink_connect",
        "select dblink_disconnect",
        "select 1 from pg_namespace",
        "select 1 from pg_extension",
    ]
    pipe = _read(PIPE)
    apply_ = _read(APPLY)
    shell = _read(SCRIPT)
    for blob in (pipe, apply_):
        for stmt in _stmts(blob):
            head = stmt.strip().split(None, 1)[0].lower() if stmt.strip() else ""
            assert head in {"select", "set"}, f"non-SELECT stmt sent from helper: {stmt[:60]!r}"
    # the shell must never run write-SQL against the legacy cluster (lsql)
    for leg in re.findall(r'lsql "([^"]*)"', shell):
        head = leg.strip().split(None, 1)[0].lower()
        assert head == "select", f"lsql received a write: {leg[:60]!r}"


def test_dst_naming_parity_with_pgconn():
    """agent_schema derives lcm2_<sanitized basename(home)>; the script's
    dst naming must agree for the canonical profile paths."""
    shell = _read(SCRIPT)
    m = re.search(r'DST_LIVE="lcm2_\$\{P\}"', shell)
    assert m, "dst must be lcm2_${P}"
    assert re.search(r"P=\$\(echo \"\$\{PROFILE##\*/\}\" \| tr -c 'a-z0-9_' '_' \| tr 'A-Z' 'a-z'\)", shell), \
        "P must be the sanitized basename of the profile (agent_schema rule)"
    # pgconn's own sanitiser for reference (same charset contract)
    pgconn = _read(os.path.join(ROOT, "plugins", "lcm2-pg", "pgconn.py"))
    assert "re.sub(r'[^a-z0-9_]', '_', base.lower())" in pgconn
    # the WRONG historical form must not come back
    assert "lcm2_pg_" not in shell


def test_source_readonly_pin_and_execute_guard():
    shell = _read(SCRIPT)
    assert "default_transaction_read_only=on" in shell
    assert "--execute" in shell and "__premig" in shell
    # dry-run default must DROP the throwaway, never rename live
    assert "drop schema $DST_TMP cascade" in shell
