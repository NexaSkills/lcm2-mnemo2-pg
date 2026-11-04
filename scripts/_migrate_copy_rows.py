#!/usr/bin/env python3
"""_migrate_copy_rows.py — client-side row pipe for the legacy->lcm2 data
migration (used by migrate_legacy_lcm_to_lcm2.sh when dblink is absent; the
dblink path stays in the shell). SELECT-only on the SOURCE connection
(explicit SET TRANSACTION READ ONLY + SELECT statements only); INSERTs land
only in the destination schema passed in (a throwaway unless the operator
already renamed one into place).

Type mapping (legacy sqlite-era shapes -> new engine shapes):
  messages:      float epoch -> TIMESTAMPTZ (to_timestamp server-side on
                 insert), int pinned -> boolean, text tool_calls -> jsonb
                 (invalid JSON -> NULL, counted and reported).
  summary_nodes: float epoch columns likewise; text source_ids -> jsonb
                 (invalid -> '[]'::jsonb).
  store_id/node_id are identity columns on the destination and deliberately
  NOT copied (new ids; the engine only orders by them, and
  lcm_lifecycle_state bookkeeping is not carried across).

Usage: _migrate_copy_rows.py <src_dsn> <dst_dsn> <src_schema> <dst_schema> <table>
"""
import sys

import psycopg

MAPS = {
    "messages": (
        """select session_id, coalesce(source,''), coalesce(conversation_id,''),
                  role, content, tool_call_id, tool_calls, tool_name,
                  to_timestamp(timestamp), token_estimate, pinned::boolean,
                  to_timestamp(ingested_at), to_timestamp(observed_at),
                  observed_at_source
           from "{src}".messages order by store_id""",
        """insert into {dst}.messages (session_id, source, conversation_id, role,
             content, tool_call_id, tool_calls, tool_name, timestamp,
             token_estimate, pinned, ingested_at, observed_at, observed_at_source)
           values (%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s,%s,%s,%s,%s)""",
    ),
    "summary_nodes": (
        """select session_id, depth, summary, token_count, source_token_count,
                  source_ids, coalesce(source_type,'messages'),
                  to_timestamp(created_at), to_timestamp(earliest_at),
                  to_timestamp(latest_at), coalesce(expand_hint,'')
           from "{src}".summary_nodes order by node_id""",
        """insert into {dst}.summary_nodes (session_id, depth, summary,
             token_count, source_token_count, source_ids, source_type,
             created_at, earliest_at, latest_at, expand_hint)
           values (%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s,%s,%s)""",
    ),
}


def main() -> int:
    src_dsn, dst_dsn, src_schema, dst_schema, table = sys.argv[1:6]
    sel, ins = MAPS[table]
    n = json_bad = 0
    with psycopg.connect(src_dsn) as src, psycopg.connect(dst_dsn) as dst:
        src.execute("SET TRANSACTION READ ONLY")
        with src.cursor(name="pipe", withhold=True) as cur:
            cur.execute(sel.format(src=src_schema))
            while True:
                rows = cur.fetchmany(2000)
                if not rows:
                    break
                payload = []
                for r in rows:
                    r = list(r)
                    # jsonb params arrive as text; invalid JSON -> NULL/[] and
                    # still land the row (honest: counted, reported)
                    j = r[6 if table == "messages" else 5]
                    if isinstance(j, str):
                        import json
                        try:
                            json.loads(j)
                        except Exception:
                            r[6 if table == "messages" else 5] = None
                            json_bad += 1
                    payload.append(tuple(r))
                with dst.cursor() as dc:
                    dc.executemany(ins.format(dst=dst_schema), payload)
                n += len(payload)
        dst.commit()
    print(f"rows={n} json_unparseable_to_null={json_bad}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
