#!/usr/bin/env python3
"""_migrate_apply_ddl.py — apply the repo's OWN lcm2 ddl.py under an
arbitrary schema (used by migrate_legacy_lcm_to_lcm2.sh to author the
throwaway destination schema with the exact engine DDL — one source of
truth, never a second DDL copy).

Usage: _migrate_apply_ddl.py <dsn> <schema>
"""
import importlib.util
import os
import sys

import psycopg


def main() -> int:
    dsn, schema = sys.argv[1], sys.argv[2]
    repo = os.environ.get("REPO_DIR") or os.path.abspath(
        os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
    path = os.path.join(repo, "plugins", "lcm2-pg", "ddl.py")
    if not os.path.isfile(path):
        sys.exit(f"_migrate_apply_ddl: ddl.py not found at {path}")
    spec = importlib.util.spec_from_file_location("_mig_ddl", path)
    ddl = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ddl)

    class Conn:  # minimal surface ddl.apply_schema needs
        def __init__(self, schema):
            self.cx = psycopg.connect(dsn, autocommit=True)
            self.schema = schema
            # ddl.apply_schema emits UNQUALIFIED CREATE TABLEs — they land in
            # the session schema, exactly like engine bootstrap's Conn does
            # (pgconn.Conn pins search_path the same way).
            self.cx.execute("select set_config('search_path', %s, false)",
                            (schema,))

        def execute(self, sql, params=None):
            return self.cx.execute(sql, params)

        def query_scalar(self, sql, params=None):
            r = self.cx.execute(sql, params).fetchone()
            return next(iter(r.values())) if r else None

        def __enter__(self):
            return self

        def __exit__(self, *a):
            self.cx.close()

    ddl.apply_schema(Conn(schema))
    return 0


if __name__ == "__main__":
    sys.exit(main())
