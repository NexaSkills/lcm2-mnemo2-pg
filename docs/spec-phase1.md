# lcm2-mnemo2-pg — Phase 1 spec (scaffolding)

Written 2026-11-01 (mike) per plan rubric R11 (per-phase written spec).
Plan: `/home/mybuddy/fleet/plans/lcm2-mnemo2-redesign.plan.md` (Phase 1).
Tests-first for Phase 2/3 are written against this layout.

## Instance

| Key | Value |
|---|---|
| Base | `/home/mybuddy/lcm2-mnemo2-pg/pg/` (`data/` cluster, `run/` socket) |
| Binaries | `/home/mybuddy/lcm-pg/pg/bin` (PostgreSQL 18.6, private install; reuse, no re-download) |
| Port | **54331** (distinct from the untouched lcm-pg cluster :54329) |
| Role | `mnemo201` (SUPERUSER, cluster owner) |
| Auth | `local all all trust` over the 0700 private socket dir — same practice as the existing lcm-pg cluster (`~/lcm-pg/instance/pg_hba.conf`) |
| Service | systemd **user** service `lcm2-mnemo2-pg.service` (enabled, `WantedBy=default.target`) |
| Isolation | `listen_addresses=''` (local socket only); existing cluster untouched |

## Databases & schemas

- `lcm2db` — private schemas only, pattern `lcm2_<agent>`. Seeded: `lcm2_lcm2agent`. **NO shared schema** (R2).
- `mnemo2db` — private schemas `mnemo2_<agent>` + one `shared` schema for cross-agent memory. Seeded: `mnemo2_mnemo2agent`, `shared`.

## Profiles (test-only, R10)

`lcm2agent`, `mnemo2agent` created via `hermes profile create --no-alias`. Config keys set ONLY via `hermes -p <p> config set` (fleet rule). Per-profile `.env` carries the DSN params (global params come from `/home/mybuddy/.hermes/.env`, profile `.env` overrides — file-editable surface).

- `LCM2_PG_DSN=postgresql:///lcm2db?host=<run>&port=54331&user=mnemo201`
- `MNEMO2_PG_DSN=postgresql:///mnemo2db?host=<run>&port=54331&user=mnemo201`
- `MNEMO2_SHARED_SCHEMA=shared`

Schema is NOT pinned in the DSN: the plugins derive `lcm2_<agent>` /
`mnemo2_<agent>` from the agent name by convention at runtime (fleet
practice — the lcm-pg deploy strips pinned `LCM_PG_SCHEMA` for the same
reason).

## Plugin symlink layout (R6)

Source lives ONCE in this checkout: `plugins/lcm2-pg/`, `plugins/mnemo-pg2/`.
Symlinked (never copied) into `~/.hermes/plugins/` and each test profile's
`plugins/` dir. One edit at source = visible to every agent on next load.
Phase 1 ships inert skeletons (discovery-valid `plugin.yaml`, no tools/hooks).

## Scripts

- `scripts/setup_instance.sh` — idempotent: initdb + unit (+ role bootstrap via
  single-user backend) + dbs + schemas. All binaries from the private PGBIN.
  Ends with `bootstrap_verify.sh` (item-0 assertion): role/db/schemas/postmaster/
  pg_hba must all be REAL, exit non-zero otherwise — an initdb leg that silently
  no-ops leaves a LISTENing :54331 with zero bootstrap state (trade/warren
  fresh-host class), invisible to `systemctl is-active`.
- `scripts/bootstrap_verify.sh [agent ...]` — the assertion itself (standalone;
  same BASE/PORT/ROLE/PGBIN env surface as setup_instance.sh).
- `scripts/env_dsn_check.sh [file ...]` — asserts every LCM2/MNEMO2 DSN line in
  root+profile .env files parses as one clean URI (merged-line = sqlite-fallback
  class); deterministic red/green, safe for check_all.
- `scripts/setup_dbs.sh [agent ...]` — idempotent: lcm2db + mnemo2db + role +
  schemas on the dedicated instance only; fails loudly if a `shared` schema ever
  appears in lcm2db (R2 guard).
- `scripts/ensure_instance.sh` — 2-min systemd-timer watchdog that self-heals
  role/hba/dbs/schemas if any rogue process breaks them (log: ~/.hermes/logs/lcm2-mnemo2-ensure.log).
- `scripts/onboard_profile.sh <agent>` — idempotent: profile, schemas, .env params, config via CLI, symlinks, verify.

## Global params (Phase 1 addendum)

`/home/mybuddy/.hermes/.env` carries the global block consumed by both plugins:
`LCM2_PG_HOST`, `LCM2_PG_PORT`, `LCM2_PG_USER`. Per-profile `.env`
(`LCM2_PG_DSN` / `MNEMO2_PG_DSN` / `MNEMO2_SHARED_SCHEMA`) overrides it
(profile-first resolution, per plan §4).

## Phase-0 polish carried (N1–N3, per QA-r2)

- N1: spec-phase0 B4 wording — the legacy `memories` table lives in core
  source (core/memory.py:372) + migration scripts, not only tests/ fixtures.
- N2: "executed form" wording for PRAGMA foreign_key_check (tests-only hits).
- N3/N4: scope-label footnote — "source-only" counts use strict `-v '/tests/'`;
  substring exclusion shifts sqlite_vec src count 44→43 (cosmetic only).

## Resume turn (2026-11-01, QA PARTIAL fixes + instance consolidation)

- Instance binary consolidation: the systemd unit was found running the distro
  build (`/usr/bin/postgres`) against a distro-initdb'd data dir. setup_instance.sh
  now rewrites AND restarts the unit whenever its ExecStart drifts from $PGBIN,
  and verifies `/proc/<postmaster>/exe == $PGBIN/postgres` before proceeding.
  Old data dir preserved as pg/data.broken-system-pg (audit only).
- QA F1: onboard_profile.sh plugin-enable/verify polish committed (was disk-only).
- QA fix 2: `onboard_profile.sh --dry-run` (no agent) lists profiles and exits 0;
  agent-arg dry-run unchanged.
- Env fix: script uses HERMES_BASE (from HERMES_REAL_HOME/$HOME), never the
  profile-scoped HERMES_HOME env var an agent shell exports.
