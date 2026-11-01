# lcm2-mnemo2-pg

Clean-room, PostgreSQL-native redesigns of the two Hermes Agent memory systems:

- **lcm2-pg** — lossless context management (redesign of
  [stephenschoettler/hermes-lcm](https://github.com/stephenschoettler/hermes-lcm)).
  DB `lcm2db`, one private schema per agent (`lcm2_<agent>`), no shared schema.
- **mnemo-pg2** — durable memory (redesign of
  [mnemosyne-oss/mnemosyne](https://github.com/mnemosyne-oss/mnemosyne)).
  DB `mnemo2db`, one private schema per agent (`mnemo2_<agent>`) plus one `shared`
  schema for cross-agent memory.

Both are PG-native from the ground up: zero sqlite code, zero sqlite fallback paths.

## Status

- **Phase 0 (recon spec): complete.** Full reverse-engineering inventory of both
  vendor repos (tools, hooks, config keys, DB tables, sqlite mechanisms) mapped to
  the native-PG design: [`docs/spec-phase0.md`](docs/spec-phase0.md).
- Phase 1 — scaffolding (new PG instance under `/home/mybuddy`, DBs, schemas,
  plugin symlink layout, profile onboarding).
- Phase 2 — `lcm2-pg` plugin (TDD).
- Phase 3 — `mnemo-pg2` plugin (TDD, private + shared).
- Deploy/update/migrate scripts land here as their phases complete.

## Layout (target)

```
docs/                 phase specs (spec-before-code)
lcm2-pg/              plugin source (tools, hooks, config docs)
mnemo-pg2/            plugin source (tools, hooks, config docs)
scripts/              new-instance setup, deploy, migrate, profile onboarding
tests/                TDD tests written from the plan rubric, per phase
```

Governed by the project plan (owner: manny; dev: mike; QA: tessa) using the
[Superpowers](https://github.com/obra/superpowers) methodology: plan-first,
tests-before-code, verification-before-assertion.
