#!/usr/bin/env python3
"""One-shot DB hygiene sweep (QA fix note F3).

Removes residue that breaks the public-is-table-free invariant, which is the
exact failure mode behind the R8 public-shadowing flake (search_path
'<schema>, public' lets public relations shadow agent-schema writes):

  * every relation (table/view/matview/partitioned table) in schema `public`
    on the lcm2db instance (vendor-named lcm_* leftovers, conc/vis/zz probes)
  * throwaway probe_* / lcm2_probe* / lcm2_test_* SCHEMAS (left by ad-hoc
    probes and crashed test runs)

Idempotent: a clean instance sweeps nothing. DSN via argv[1] or the
LCM2_PG_DSN / lcm2-pg defaults (same rules as pgconn.default_dsn).
"""
import os
import re
import subprocess
import sys

DSN = sys.argv[1] if len(sys.argv) > 1 else (
    os.environ.get("LCM2_PG_DSN")
    or "dbname=lcm2db host=%s port=54331 user=mnemo201"
    % os.path.expanduser("~/lcm2-mnemo2-pg/pg/run"))

BAD_SCHEMA = re.compile(r"^(probe_.*|lcm2_probe.*|lcm2_test_.*)$")


def psql(sql):
    r = subprocess.run(["psql", DSN, "-At", "-v", "ON_ERROR_STOP=1", "-c", sql],
                       capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit(f"psql failed: {r.stderr.strip()}")
    return r.stdout.strip()


def main():
    rels = psql(
        "select c.relname from pg_class c "
        "join pg_namespace n on n.oid = c.relnamespace "
        "where n.nspname = 'public' "
        "and c.relkind in ('r','p','m','v','f') order by 1").splitlines()
    for rel in rels:
        psql(f'drop table if exists public."{rel}" cascade')
        print(f"dropped public.{rel}")

    schemas = [s for s in psql(
        "select nspname from pg_namespace order by 1").splitlines()
        if BAD_SCHEMA.match(s)]
    for s in schemas:
        psql(f'drop schema if exists "{s}" cascade')
        print(f"dropped schema {s}")

    # conc_probe leftovers anywhere (the zz diagnostic writes probe tables
    # into agent schemas, never public): drop them so the public-free
    # invariant the R8 suite depends on holds in every schema.
    probes = psql(
        "select n.nspname || '.' || c.relname from pg_class c "
        "join pg_namespace n on n.oid = c.relnamespace "
        "where c.relname = 'conc_probe' and n.nspname not in"
        " ('pg_catalog','information_schema') order by 1").splitlines()
    for fq in probes:
        s, rel = fq.split(".", 1)
        psql(f'drop table if exists "{s}"."{rel}" cascade')
        print(f"dropped {fq}")

    left = int(psql(
        "select count(*) from pg_class c "
        "join pg_namespace n on n.oid = c.relnamespace "
        "where n.nspname = 'public' "
        "and c.relkind in ('r','p','m','v','f')"))
    bad_left = [s for s in psql(
        "select nspname from pg_namespace").splitlines() if BAD_SCHEMA.match(s)]
    print(f"RESULT public_relations={left} bad_schemas={len(bad_left)}")
    if left or bad_left:
        sys.exit("sweep incomplete")


if __name__ == "__main__":
    main()
