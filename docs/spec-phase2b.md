# lcm2-mnemo2-redesign — Phase 2 spec addendum B (chunk B2): FULL tool-surface parity

Slug: lcm2-mnemo2-redesign · Phase: 2 · Worker: mike · Date: 2026-01 (B2 chunk)
Base: docs/spec-phase2.md (sections 1–7 unchanged; this addendum binds for R4
tool-surface parity). Token: LCM2-MNEMO2-P2B2.

## B1. Authoritative tool enumeration (source-enumeration wildcard rule)

Like the A4 table rule for storage, the authoritative TOOL set is enumerated
FROM the pinned vendor source (`/home/mybuddy/lcm-upstream` @ a13e709), not
from any itemized prose. Two wildcards, unioned:

```
grep -rhoE '"name": *"lcm_[a-z_0-9]+"' <src> --include='*.py' \
     --exclude-dir=tests --exclude-dir=__pycache__ | grep -oE 'lcm_[a-z_0-9]+' | sort -u   # schemas.py dicts
grep -rhoE '"(lcm_[a-z_0-9]+)": *[a-zA-Z_]'  <src>/engine.py                              # dispatch map values
```

At a13e709 both yield the SAME 15 names — `provides_tools` (15) =
`get_tool_schemas` (15) = `handle_tool_call` dispatch map (15). No hidden
16th tool exists in the vendor source. tests/test_r4_full_surface.py
re-enumerates this live and asserts the union equals what the plugin
registers (drift detector).

## B2. Extras parity (A1 "Extras" row, mirrored)

- `/lcm` slash command — env-gated `LCM_ENABLE_SLASH_COMMAND` (vendor:
  command.py `KNOWN_SUBCOMMANDS = ("status", "doctor", "inspect")` +
  usage). lcm2-pg keeps `status|inspect|doctor|grep <q>` documented in its
  usage line.
- bundled skill — vendor `register_skill` points at
  `<vendor>/skills/hermes-lcm`; lcm2-pg ships `skills/lcm2-pg/SKILL.md`
  with identical frontmatter name (vendor bundle is doc content; copying
  it wholesale would import vendor claims that don't describe this code).
- context engine + hooks: unchanged from spec §2.

## B3. Real-DB functional proofs (beyond "returns JSON")

The B2 test family exercises every one of the 15 tools against the REAL
instance (lcm2db:54331, throwaway schema) with a seeded session (30
messages with distinct markers + compression run):

| Tool | real-DB assertion |
|---|---|
| lcm_grep | seeded marker found in results |
| lcm_recall | seeded marker found in hits |
| lcm_recent | window parses; rollups/leaf-fallback shape |
| lcm_load_session | ≥1 seeded message; next_cursor pagination |
| lcm_expand(store_id) | returns seeded message content verbatim |
| lcm_expand(node_id) | recursive DAG sources after compression |
| lcm_describe() | dag_overview non-empty after compression |
| lcm_describe(node_id) | child_count + expand_hint |
| lcm_expand_query | context includes node summary content |
| lcm_query_state | assertion INSERTED into lcm_assertions, then read back (as_of/kinds filters) |
| lcm_compute | count + sum computed from supplied operands |
| lcm_compile_evidence | ref_count echoed from supplied refs |
| lcm_evidence_pack | real store_id ref hydrates real content |
| lcm_retrieve | start→search×3 (4th rejected)→status→finish lifecycle |
| lcm_status | counts reflect seeded messages |
| lcm_inspect | fresh_tail shows the session; lifecycle row present |
| lcm_doctor | healthy on clean schema; schema_version check |

Honesty rule (§2) still applies: `simplified` annotations stay where a deep
vendor sub-mechanism is not ported; the tests assert the tool's core
contract works, not that simplified mechanics exist.
