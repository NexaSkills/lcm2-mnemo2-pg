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
   plugin symlinks (profile dir -> this checkout's `plugins/`), verify.
   Shared-host install (one repo serving every profile): prefix with
   `LCM2_ONBOARD_GLOBAL=1` to also author the global
   `$HERMES_HOME/plugins` links; off by default so a QA/fresh-clone run
   never rewires other profiles. `--dry-run` prints intended changes.
4. `bash scripts/apply_surface_fixes.sh` (ALL profiles, idempotent; proven on
   beez 2026-11-05) — the three surface classes that code alone does not
   cover: (a) systemd drop-ins ordering gateway/webui/dashboard
   AFTER/WANTS `lcm2-mnemo2-pg.service` (cold-boot race cure: without it a
   lane beats the :54331 cluster and silently falls back to the built-in
   compressor); (b) repo-owned threshold params — `LCM_CONTEXT_THRESHOLD=0.75`
   in root + every profile `.env` (lcm2-pg = defacto compressor, fires at 75%
   of window) and `compression.threshold 0.95` per profile via
   `hermes config set` (built-in compressor = safeguard, instantiated only
   when no engine loaded); (c) legacy disable — `lcm-pg` (and `mnemo-pg` v1
   where present) disabled root + every profile, stale probe cluster units
   (fix8/qa6/split) disabled so they cannot crash-loop at boot.
5. Update = `git pull` + re-run setup scripts + `apply_surface_fixes.sh`
   (all idempotent). Plugin code is symlinked, so a pull is live on next
   session load — no per-profile copies. Then bounce gateway/webui/dashboard.
6. Migrate: `scripts/migrate_legacy_lcm_to_lcm2.sh` (legacy old->new data,
   idempotent; schema creation itself is automatic — engine bootstrap runs
   `CREATE SCHEMA IF NOT EXISTS`, `setup_dbs.sh` the bank + `shared`).

Layout details: `docs/spec-phase1.md`. Per-phase specs: `docs/spec-phaseN.md`.

## Requirements — postgres binaries

The dedicated cluster reuses the Postgres binaries already installed by the
lcm-pg fleet standard: `PGBIN` defaults to `~/lcm-pg/pg/bin` (`postgres`,
`initdb`, `psql`, `pg_ctl`). The instance scripts (`setup_instance.sh`,
`ensure_instance.sh`) refuse to run if that directory is missing or lacks
`postgres` — install/lcm-pg first, or export `PGBIN=/path/to/pg/bin` before
running them. This is a deliberate shared-dependency (QA F5): one binary set,
two clusters (`lcm-pg` :54329, `lcm2-mnemo2-pg` :54331), no duplicate installs.
