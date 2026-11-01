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

## Deploy / update / migrate (Phase 1 scaffold)

1. `git clone https://github.com/NexaSkills/lcm2-mnemo2-pg ~/lcm2-mnemo2-pg && cd ~/lcm2-mnemo2-pg`
2. `bash scripts/setup_instance.sh lcm2agent mnemo2agent`
   — initdb the dedicated cluster under `~/lcm2-mnemo2-pg/pg` (port 54331,
   systemd user service `lcm2-mnemo2-pg.service`), create `lcm2db` +
   `mnemo2db`, seed agent schemas + mnemo2 `shared`. Idempotent; never touches
   the existing lcm-pg cluster (:54329). Env overrides: `BASE PORT ROLE PGBIN SERVICE`.
3. `bash scripts/onboard_profile.sh <agent>` per test agent
   — profile create, schemas, `.env` params, config via `hermes config set`,
   plugin symlinks (global + profile -> this checkout's `plugins/`), verify.
   `--dry-run` prints intended changes.
4. Update = `git pull` + re-run the two scripts (idempotent). Plugin code is
   symlinked, so a pull is live on next session load — no per-profile copies.
5. Migrate: none yet (Phase 2/3 define vendor→native data migration; scripts
   will land here as `scripts/migrate_*.sh`).

Layout details: `docs/spec-phase1.md`. Per-phase specs: `docs/spec-phaseN.md`.

## Requirements — postgres binaries

The dedicated cluster reuses the Postgres binaries already installed by the
lcm-pg fleet standard: `PGBIN` defaults to `~/lcm-pg/pg/bin` (`postgres`,
`initdb`, `psql`, `pg_ctl`). The instance scripts (`setup_instance.sh`,
`ensure_instance.sh`) refuse to run if that directory is missing or lacks
`postgres` — install/lcm-pg first, or export `PGBIN=/path/to/pg/bin` before
running them. This is a deliberate shared-dependency (QA F5): one binary set,
two clusters (`lcm-pg` :54329, `lcm2-mnemo2-pg` :54331), no duplicate installs.
