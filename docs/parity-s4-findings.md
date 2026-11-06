# S4 edge-param audit vs vendor contract (2026-11-06)

Method: tests/mnemo2/test_s4_edge_params.py drives every optional-parameter
boundary through the same `_dispatch` the host calls (empty/oversized/negative/
unknown-enum inputs on remember, recall weights+banks, canonical replace,
triple supersede + as_of, media minimal, persona ops on missing rows, get/
forget/update/validate misses, stats/diagnose shape).

Verdict: NO product deviations found.
- `_dispatch` never raises into the host on any probe (error dicts out).
- remember: importance 0.0/1.0 accepted, empty content rejected as dict,
  invalid valid_until -> error dict (vendor same).
- recall: weight extremes and bank='shared' behave per contract.
- canonical: replace keeps history; recall_canonical serves latest.
- triples: supersede closes prior validity; triple_query returns vendor's
  `facts` key (test-side naming mismatch only, product already parity).
- persona ops on missing rows: error dicts, no side effects.

One pre-existing test-suite artifact remains (test_shared_cross_agent
teardown ERROR, reproduced at clean HEAD earlier) - unrelated to lanes.
