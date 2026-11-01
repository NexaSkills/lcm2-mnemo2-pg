---
name: lcm2-pg
description: Use, operate, and diagnose the lcm2-pg PG-native lossless context plugin (lcm2db, schema lcm2_<agent>).
---

# lcm2-pg

PG-native clean-room of hermes-lcm for the `lcm2agent` scope. Every message
is persisted (lossless); summaries are DAG-folded; all storage is psycopg on
`lcm2db` (port 54331), one private schema `lcm2_<agent>` — zero sqlite.

Recall workflow (same tool names as vendor):

1. `lcm_grep` / `lcm_recall` to locate, `lcm_expand(store_id=|node_id=)` to
   recover exact content, `lcm_describe` for DAG metadata.
2. `lcm_status`, `lcm_inspect`, `lcm_doctor` before changing anything.
3. Stateful evidence: `lcm_query_state` (assertions), then `lcm_compute` /
   `lcm_evidence_pack` / `lcm_compile_evidence` over exact cited refs.
4. Slash: `/lcm [status|inspect|doctor|grep <q>]`.

Simplifications vs vendor are declared explicitly in each tool result
(`"simplified": [...]`) — never silent.
