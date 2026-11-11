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
- **Vendor parity (S program): complete.** `mnemo-pg2` now matches the
  deployed vendor `mnemosyne` behaviour on every lane (S1-S4 below;
  source-verified against the pinned vendor tree). One documented
  deviation: none outstanding at behaviour level.

### Vendor-parity lanes in mnemo-pg2 (S1-S4, 2026-11-06)

| Lane | Vendor counterpart | Switch (default) |
|---|---|---|
| Auto-capture of every primary-context turn (sanitizer + dedup ledger) | `sync_turn` | `MNEMO2_AUTO_CAPTURE` (off in code; rollout pins `1` in root `.env`) |
| write_policy context-skip (cron/subagent/flush/background/skill_loop + cron session-id shape) | `_skip_contexts` | `MNEMO2_SKIP_CONTEXTS`, `MNEMO2_SKIP_CRON_SESSIONS` (on) |
| Typed fact annotations on every stored memory (pure regex, zero-model) + always-on heuristic KG ingest | `extract_and_store_facts` / `_ingest_graph` | `MNEMO2_AUTO_FACTS`, `MNEMO2_AUTO_TRIPLES` (on) |
| Fact-hit ranking boost in recall | fact ranking | always on |
| Recall reinforcement (`recall_count` / `last_recalled`) | beam reinforcement | always on |
| Consolidation at session end + turn-opportunistic auto-sleep (threshold 50 eligible rows) | `beam.sleep` / `_maybe_auto_sleep` | `MNEMO2_AUTO_CONSOLIDATE` (off in code; rollout pins `1`), `MNEMO2_AUTO_SLEEP` (on), `MNEMO2_AUTO_SLEEP_THRESHOLD` |
| Cross-process reflection budget (single-flight; `meta` table claims; LLM calls serialize behind `mnemo2_llm_slot`) | `_reserve_reflection_budget` | always on |
| LLM consolidation summaries via the configured proxy (concat fallback when unreachable) | reflection | `MNEMO2_LLM_URL` / `MNEMO2_LLM_MODEL` / `MNEMO2_LLM_KEY` |
| Semantic embeddings: local fastembed ONNX (default `BAAI/bge-small-en-v1.5`, 384-dim), separate doc/query lanes with env prefixes, pgvector storage + HNSW cosine index; API mode; deterministic hashed fallback offline | `embeddings.py` (fastembed/API) | `MNEMO2_EMBED_MODEL` (`fastembed[:model]` / `hashed` / api), `MNEMO2_EMBED_CACHE`, `MNEMO2_EMBED_DOC_PREFIX` / `MNEMO2_EMBED_QUERY_PREFIX`. NEVER the main/active LLM (import-graph guarded) |

`scripts/embed_migrate.sh [schema...]` performs the one-time embedding
migration per host (dim ALTER with dependent-view drop/rebuild, batched
re-embed from stored source text, model tagging, HNSW indexes; resumable).
`scripts/consolidate_stale.sh` is a MANUAL on-demand hygiene pass (deliberately
not installed as a timer anywhere; parity triggers are event-driven).
Edge-parameter audit vs the vendor contract: `docs/parity-s4-findings.md`
(zero deviations).

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

> **Routine update of an already-deployed host (the normal lane since S5):**
> `bash scripts/update_mnemo_host.sh` — pull -> instance watchdog -> dbs ->
> surfaces (incl. fastembed provisioning in ALL installed envs + resumable
> `embed_migrate.sh`) -> read-only `check_mnemo.sh`. Services are never
> bounced by scripts; when a bounce is warranted the script prints the
> verified `systemctl --user restart …` line for the operator to run.
> `check_mnemo.sh` runs standalone any time (exit 0 = healthy, WARNs are
> advisory).

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
   reference deployment) — the three surface classes that code alone does not
   cover: (a) systemd drop-ins ordering gateway/webui/dashboard
   AFTER/WANTS `lcm2-mnemo2-pg.service` (cold-boot race cure: without it a
   lane beats the :54331 cluster and silently falls back to the built-in
   compressor); (b) repo-owned threshold params — `LCM_CONTEXT_THRESHOLD=0.75`
   in root + every profile `.env` (lcm2-pg = defacto compressor, fires at 75%
   of window) and `compression.threshold 0.95` per profile via
   `hermes config set` (built-in compressor = safeguard, instantiated only
   when no engine loaded); (c) legacy disable — `lcm-pg` (and `mnemo-pg` v1 where present) disabled
   root + every profile, stale probe cluster units (fix8/qa6/split) disabled
   so they cannot crash-loop at boot; (d) memory-provider slot —
   `memory.provider mnemosyne` root + every profile via `config set`
   ('mnemo-pg2' fails the vendor provider-dir gate = silent built-in memory).
5. Update = `git pull` + re-run setup scripts + `apply_surface_fixes.sh`
   (all idempotent). Plugin code is symlinked, so a pull is live on next
   session load — no per-profile copies. Then bounce gateway/webui/dashboard.
6. Migrate: `scripts/migrate_legacy_lcm_to_lcm2.sh` (legacy old->new data,
   idempotent; schema creation itself is automatic — engine bootstrap runs
   `CREATE SCHEMA IF NOT EXISTS`, `setup_dbs.sh` the bank + `shared`).
7. Prove losslessness with the canary test: `docs/canary-lossless-test.md`
   (operator-driven prompt protocol — anchors planted in a real session,
   then recovered verbatim from the store after the first fold. A
   rollout is not "proven lossless" until this passes on the host).

Layout details: `docs/spec-phase1.md`. Per-phase specs: `docs/spec-phaseN.md`.

## Requirements — python envs and DSN resolution (fleet incidents 2026-11-10)

- **psycopg in EVERY loading env.** The plugins import `psycopg` (v3) and
  `psycopg_pool` at load time; a missing wheel fails the lane silently
  (`Memory provider ... loaded but no provider instance found` /
  `Context engine ... not found — falling back to built-in compressor`)
  or via the loader's 10s timeout lane (X-4 class: `hermes update` rebuilds
  envs WITHOUT psycopg). Envs that must each carry it: every
  `~/.hermes/tools/python-*/bin/python3` (the gateway's isolated launcher)
  AND every `~/.hermes/installs/*/environments/*/venv/` — an env rebuild
  mid-life silently re-breaks only ONE of them. `apply_surface_fixes.sh`
  installs psycopg into all installed envs (idempotent); `check_mnemo.sh`
  imports it in every env and FAILs on any miss. After any `hermes update`
  on a fleet host, re-run `check_mnemo.sh` before trusting the lanes.
- **Socket-only cluster → socket DSNs.** The dedicated cluster listens on
  the unix socket in `<BASE>/pg/run` only (`listen_addresses=''`); a
  TCP probe of `:54331` FAILS CLOSED against it. `default_dsn()` composes
  `host=<BASE>/pg/run` from the golden defaults, but hosts running stale
  checkouts (old `pgconn`) or odd homes can fall back to TCP and die with
  the warnings above. Pin it explicitly in the root AND every profile
  `.env`:
  `LCM2_PG_DSN=postgresql:///lcm2db?host=<BASE>/pg/run&port=54331&user=mnemo201`
  `MNEMO2_PG_DSN=postgresql:///mnemo2db?host=<BASE>/pg/run&port=54331&user=mnemo201`
  `MNEMO2_SHARED_SCHEMA=shared`
  (`onboard_profile.sh` writes these per profile; `update_mnemo_host.sh`
  re-verifies).
- **Self-healing.** `ensure_instance.sh` repairs the cluster + role,
  `apply_surface_fixes.sh` repairs envs/params/drop-ins, and
  `check_mnemo.sh` is the verdict: a rollout/update is DONE only when a
  `check_mnemo: FAIL=0` line has been OBSERVED on the host (WARNs
  advisory). All scripts are idempotent and safe to re-run.
- **Repo pg dirs are runtime state, never source.** `pg/data*`, `pg/run/`,
  `pg/log/` are gitignored (a disk-clone of a deployed host must not ship
  another host's databases or socket locks in the checkout).

## Requirements — plugin placement standard (Jan, 2026-11-11)

- **The hub is the only plugin surface.** Every plugin RESIDES in
  `~/.hermes/plugins/<name>` as a REAL directory, materialised from the git
  checkout by `plugin_hub.sh` (`rsync -c`, wired into
  `update_mnemo_host.sh`). The checkout is the single write-source; the hub
  copy is never hand-edited (`--check` diffs it content-wise and FAILs on
  drift). Every profile (except the root/default surface, which reads the
  hub directly) gets SYMLINKS `~/.hermes/profiles/<p>/plugins/<name> ->
  ~/.hermes/plugins/<name>` — never links straight into a checkout.
  Exception: `context_engine/lcm2-pg` stays a symlink into the checkout
  (route-1 engine resolver loads exactly ONE code path; a materialised
  second copy would violate the single-source invariant).
- **Self-contained hosts keep everything under ~/.hermes.** With
  `LCM2_SELF_CONTAINED=1` in the root `.env` (or one-off
  `update_mnemo_host.sh --relocate`), the relocation leg moves the checkout
  to `~/.hermes/checkout/lcm2-mnemo2-pg` (the DB instance under
  `~/lcm2-mnemo2-pg/pg` lives OUTSIDE the checkout and is untouched).
  Fail-closed everywhere: foreign hub entries, existing relocation
  targets, and real-dir hub entries the script doesn't own are FAILs,
  never silent overwrites. `check_mnemo.sh` (lane 8) reports the verdict.

## Requirements — postgres binaries

The dedicated cluster reuses the Postgres binaries already installed by the
lcm-pg fleet standard: `PGBIN` defaults to `~/lcm-pg/pg/bin` (`postgres`,
`initdb`, `psql`, `pg_ctl`). The instance scripts (`setup_instance.sh`,
`ensure_instance.sh`) refuse to run if that directory is missing or lacks
`postgres` — install/lcm-pg first, or export `PGBIN=/path/to/pg/bin` before
running them. This is a deliberate shared-dependency (QA F5): one binary set,
two clusters (`lcm-pg` :54329, `lcm2-mnemo2-pg` :54331), no duplicate installs.
