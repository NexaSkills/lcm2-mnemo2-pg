#!/usr/bin/env bash
# clone_prep.sh — turn a fresh buddyos-template disk clone into a customer
# system (buddyos-<name>). Run ON THE CLONE, logged in as mybuddy, right
# after first boot. The clone source (template) must already be clean;
# this script never fixes other hosts.
#
# What it does (stages, in order — see stage_* functions):
#   0 prereq     clone-source sanity: sudo/linger/launcher-env/PG+vector
#   1 inputs     interactive: hostname (buddyos-<name> enforced), LAN IP,
#                webapp username+password, LiteLLM team key (paste),
#                LiteLLM master key (paste; memory-only, never on disk)
#   2 identity   hostnamectl + /etc/hosts + /etc/machine-id + dbus id +
#                fresh hermes install_id
#   3 ssh        host keys regenerated (rm + ssh-keygen -A: -A ALONE SKIPS
#                existing — pitfall), authorized_keys = sole nexa deploy key,
#                known_hosts wiped
#   4 wipe       template residue: backup everything first to
#                ~/.hermes/backups/prep-<UTC>/ (700), then delete sessions/
#                state.db*/state//memories/kanban*/projects.db/cron/
#                gateway_state.json/gateway-starts.log/pairing//
#                pending_messages//spawn-ledger.*/channel_directory.json/
#                telemetry/ + model caches + TRUNCATE every table in every
#                lcm2_*/mnemo2_*/shared schema (schemas kept, per Jan 2026-11-11)
#   5 config     new LiteLLM key into .env (key_env), auth.json pool reset,
#                webapp.enabled=true host=0.0.0.0, dashboard basic_auth
#                (username + hash_password via plugins.dashboard_auth.basic —
#                plaintext never persisted), webapp bundle build
#   6 grant      LiteLLM /kev passthrough for team <name>: probe, resolve
#                team_id via /v2/team/list, POST /team/update merging
#                metadata (README-sanctioned shape), re-probe 200.
#                Key alias convention <name>-hermes (WARN if mismatch).
#   7 heal       git pull both ~/.hermes/checkout repos + run their
#                self-healing update scripts (git pull IS the deploy)
#   8 bounce     systemctl --user restart hermes-gateway (the sd_bus legs of
#                bounce_gateway.py return -22 on systemd 262), wait for the
#                registration trio in agent.log newer than the restart
#   9 gates      PASS/FAIL each: :9120 0.0.0.0 LISTEN, http://<LAN IP>:9120/
#                -> 302, bootstrap_verify OK, check_mnemo FAIL=0, kev probe
#                200, boot registration trio. Exit non-zero if any FAIL.
#
# Secrets policy (hard): master key + team key are read with read -rs and
# live ONLY in shell variables; curl receives headers via `curl -K -` on
# stdin so nothing lands in argv/ps/log/history. Nothing secret is ever
# written to the log file.
#
# Flags:
#   --dry-run          log every mutation instead of performing it
#   --source-only      define functions and exit (test harness surface)
#   --self-test        run pure-helper assertions and exit
#   --skip-grant       do not touch LiteLLM (grant handled out-of-band)
#   --noninteractive   no prompts: hostname/ip/web-user via flags; stdin
#                      carries EXACTLY two lines: team key, then dashboard
#                      password (piped by provision_clone.sh; never argv).
#                      Grant must pre-exist orchestrator-side; stage 6
#                      probe-only.
#   --web-user=NAME    dashboard username (default: <short name>)
#   --hostname=NAME    preset (still validated)
#   --ip=ADDR          preset LAN IP (else default: current primary IPv4)
#   --deploy-key-file=PATH   use this pubkey line instead of the built-in
#
# Resume: rerun-safe (idempotent); --only-stage=N runs a single stage.
set -uo pipefail

HERMES=${HERMES_HOME:-$HOME/.hermes}
BIN=$HERMES/hermes-agent/.hermes/bin/hermes
LITELLM_URL=${LITELLM_URL:-https://ai.nexaskills.co.za}
BASE=${BASE:-$HOME/lcm2-mnemo2-pg/pg}
PORT=${PORT:-54331}
ROLE=${ROLE:-mnemo201}
PGBIN=${PGBIN:-$HOME/lcm-pg/pg/bin}
PY_DEFAULT=$(ls -d "$HOME"/.hermes/tools/python-*/bin/python3 2>/dev/null | head -1)
PY=${PY:-${PY_DEFAULT:-python3}}
# Deploy key: PUBLIC key material of the fleet admin (buddyos-nexa).
# Verified fingerprint SHA256:TO5YSi0LzDK6ltqoqpAvrK00NlWOdMKa9IXe8TADgBM
DEPLOY_KEY_PUB="ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAICvFafSAhZ7Qe2rMx3cqGT8OrClxGYV6QauWVOhBnhqB mybuddy@buddyos-nexa"
TS=$(date -u +%Y%m%d-%H%M%S)
LOGDIR=$HERMES/logs
LOGFILE=$LOGDIR/clone-prep-$TS.log
BACKUP_DIR=$HERMES/backups/prep-$TS

DRY=0; SOURCE_ONLY=0; SELFTEST=0; SKIP_GRANT=0; ONLY_STAGE=""
PRESET_HOSTNAME=""; PRESET_IP=""
NONINTER=0; WEB_USER_PRESET=""
GATES=(); FAIL_ANY=0

for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY=1 ;;
    --source-only) SOURCE_ONLY=1 ;;
    --self-test) SELFTEST=1 ;;
    --skip-grant) SKIP_GRANT=1 ;;
    --noninteractive) NONINTER=1 ;;
    --web-user=*) WEB_USER_PRESET=${arg#*=} ;;
    --hostname=*) PRESET_HOSTNAME=${arg#*=} ;;
    --ip=*) PRESET_IP=${arg#*=} ;;
    --deploy-key-file=*) DEPLOY_KEY_PUB=$(cat "${arg#*=}") ;;
    --only-stage=*) ONLY_STAGE=${arg#*=} ;;
    *) echo "unknown flag: $arg" >&2; exit 2 ;;
  esac
done

log() { local line="$*"; echo "$(date -u +%H:%M:%S) $line" | tee -a "$LOGFILE"; }
gate() { # gate NAME PASS|FAIL detail
  GATES+=("$1=$2"); [ "$2" = FAIL ] && FAIL_ANY=1
  log "GATE: $1 $2 ${3:-}"
}
run() { # run <cmd...>  — the single mutation funnel (dry-run aware)
  if [ "$DRY" = 1 ]; then log "DRY: $*"; return 0; fi
  "$@" >>"$LOGFILE" 2>&1
}
srun() { # srun <cmd...> — sudo variant
  if [ "$DRY" = 1 ]; then log "DRY: sudo $*"; return 0; fi
  sudo "$@" >>"$LOGFILE" 2>&1
}

# ---------- pure helpers (unit-tested via --source-only) ----------

validate_hostname() { # buddyos-<name>, name = lowercase slug
  local h=$1
  [[ $h =~ ^buddyos-[a-z][a-z0-9]*(-[a-z0-9]+)*$ ]]
}
short_name() { printf '%s' "${1#buddyos-}"; }        # buddyos-newclone -> newclone
team_alias()   { printf '%s-hermes' "$(short_name "$1")"; }  # API key alias: <name>-hermes
is_ipv4()      { [[ $1 =~ ^((25[0-5]|2[0-4][0-9]|1[0-9]{2}|[1-9]?[0-9])\.){3}(25[0-5]|2[0-4][0-9]|1[0-9]{2}|[1-9]?[0-9])$ ]]; }

hosts_render() { # hosts_render <file> <ip> <hostname>  -> new content on stdout
  local f=$1 ip=$2 h=$3
  awk -v ip="$ip" -v h="$h" '
    /^[[:space:]]*#/ {print; next}
    $2 == h || $1 ~ /^[0-9.]+$/ && $2 ~ /^buddyos-/ {next}   # drop stale host lines
    {print}
    END {print ip "\t" h " # clone-prep"}' "$f"
}

env_set_key() { # env_set_key <file> <KEY>  — new value read from stdin line 1
  local f=$1 k=$2 v
  IFS= read -r v
  local tmp="$f.clone-prep.tmp"
  awk -v k="$k" -v v="$v" 'BEGIN{hit=0}
    { if ($0 ~ "^"k"=") {print k"="v; hit=1; next} print }
    END { if (!hit) print k"="v }' "$f" > "$tmp" && mv "$tmp" "$f"
  chmod 600 "$f"
}

py_json_reset_credpool() { # auth.json: blank credential_pool, keep the rest
  local f=$1
  "$PY" - "$f" <<'PYX'
import json, sys
p = sys.argv[1]
d = json.load(open(p))
d["credential_pool"] = {}
json.dump(d, open(p, "w"), indent=2)
PYX
}

hash_password() { # password on stdin -> scrypt$n$r$p$salt$dk hash on stdout.
  # Vendor-EQUIVALENT, deliberately NOT importing plugins.dashboard_auth.basic:
  # that import chain pulls httpx (provider plumbing) and dies on launcher
  # envs without it (template 2026-11-11). Constants mirror the vendor
  # provider (scrypt N=2**14 r=8 p=1 dk=32 salt=16) so hashes verify.
  "$PY" -c 'import sys,secrets,hashlib,base64
pw=sys.stdin.buffer.readline().decode().rstrip("\n")
salt=secrets.token_bytes(16)
dk=hashlib.scrypt(pw.encode("utf-8"),salt=salt,n=2**14,r=8,p=1,dklen=32,maxmem=0)
print("scrypt$16384$8$1$"+base64.b64encode(salt).decode()+"$"+base64.b64encode(dk).decode())'
}

# curl wrapper: secrets arrive on stdin as 'header = "..."' config lines so
# they never appear in argv (ps-visible) nor in the log.
curl_stdin_header() { # curl_stdin_header <url> <header-line> [curl args...]
  local url=$1 hdr=$2; shift 2
  if [ "$DRY" = 1 ]; then log "DRY: curl $url [$hdr]"; echo 000; return 0; fi
  printf 'header = "%s"\nurl = "%s"\nsilent\nshow-error\noutput = "/dev/null"\nwrite-out = "%%{http_code}"\n' \
    "$hdr" "$url" | curl -K - "$@" 2>>"$LOGFILE"
}

# Typed-wire kev probe (schema from plugin kev_decide._walk, live-verified
# 2026-11-12: {"model":"kev","state":"<str>","questions":{qid:{type,
# instructions,criteria}}} -> 200 + answers). A body without state/questions
# returns 400 FROM KEV ITSELF — which proves passthrough works, but we
# assert on 200 only.
KEV_PROBE_BODY='{"model":"kev","state":"Health probe. No task running.","questions":{"skill":{"type":"choice","instructions":"Which skill fits this task? Choose none if none apply.","criteria":{"github":"GitHub repo operations","none":"No skill applies."}}}}'
probe_kev() { # -> http code of POST /kev/systemone with the team key
  curl_stdin_header "$LITELLM_URL/kev/systemone" "Authorization: Bearer $TEAM_KEY" -X POST \
    -H 'Content-Type: application/json' -d "$KEV_PROBE_BODY"
}

# ---------- stages ----------

stage_0() {
  log "== stage 0: prereq =="
  [ "$(id -un)" = mybuddy ] || { log "FAIL: run as mybuddy"; exit 1; }
  sudo -n true || { log "FAIL: passwordless sudo required"; exit 1; }
  [ "$(loginctl show-user mybuddy -p Linger --value 2>/dev/null)" = yes ] \
    || gate linger FAIL "user services die at logout"
  PY=$(ls -d "$HERMES"/tools/python-*/bin/python3 2>/dev/null | head -1); PY=${PY:-python3}
  [ -x "$BIN" ] || { log "FAIL: hermes binary missing ($BIN)"; exit 1; }
  # clone-source sanity: dedicated cluster + vector (G-1: clones never rebuild)
  local vec
  vec=$("$PGBIN/psql" -h "$BASE/run" -p "$PORT" -U "$ROLE" -d mnemo2db -Atc \
        "select extversion from pg_extension where extname='vector'" 2>>"$LOGFILE") || vec=""
  [ -n "$vec" ] || { log "FAIL: pgvector missing on clone source — fix TEMPLATE via repo, re-clone"; exit 1; }
  local mc
  mc=$("$PGBIN/psql" -h "$BASE/run" -p "$PORT" -U "$ROLE" -d mnemo2db -Atc "show max_connections" 2>>"$LOGFILE") || mc="?"
  log "prereq ok: vector=$vec max_connections=$mc PY=$PY"
}

stage_1() {
  log "== stage 1: inputs =="
  HOST_NEW=$PRESET_HOSTNAME
  if [ "$NONINTER" = 1 ]; then
    # Provisioned by scripts/provision_clone.sh from buddyos-nexa.
    # Contract: hostname/ip/user via flags; EXACTLY two secret lines on
    # stdin (line 1 = LiteLLM team key, line 2 = dashboard password).
    # The /kev grant must already exist (orchestrator-side) — stage 6
    # probe only proves it.
    validate_hostname "$HOST_NEW" || { log "FAIL: --noninteractive requires a valid --hostname"; exit 1; }
    LAN_IP=$PRESET_IP
    [ -n "$LAN_IP" ] || LAN_IP=$(ip -4 -o addr show scope global | awk '{print $4}' | cut -d/ -f1 | head -1)
    is_ipv4 "$LAN_IP" || { log "FAIL: not an IPv4: $LAN_IP"; exit 1; }
    read -r TEAM_KEY || { log "FAIL: stdin closed before team key line"; exit 1; }
    [ -n "$TEAM_KEY" ] || { log "FAIL: empty team key line"            ; exit 1; }
    read -r P1       || { log "FAIL: stdin closed before password line"; exit 1; }
    [ -n "$P1" ]      || { log "FAIL: empty password line"             ; exit 1; }
    P2=$P1
    MASTER_KEY=""
  else
  while ! validate_hostname "$HOST_NEW"; do
    read -r -p "new hostname (must match buddyos-<name>): " HOST_NEW \
      || { log "FAIL: stdin closed before a valid hostname"; exit 1; }
  done
  NAME=$(short_name "$HOST_NEW"); TEAM=$NAME; KEY_ALIAS=$(team_alias "$HOST_NEW")
  LAN_IP=$PRESET_IP
  if [ -z "$LAN_IP" ]; then
    LAN_IP=$(ip -4 -o addr show scope global | awk '{print $4}' | cut -d/ -f1 | head -1)
    read -r -p "LAN IP for $HOST_NEW [$LAN_IP]: " ans; LAN_IP=${ans:-$LAN_IP}
  fi
  is_ipv4 "$LAN_IP" || { log "FAIL: not an IPv4: $LAN_IP"; exit 1; }
  read -r -p "webapp username [mybuddy]: " WEB_USER; WEB_USER=${WEB_USER:-mybuddy}
  while :; do
    read -r -s -p "webapp password: "        P1 || { log "FAIL: stdin closed at password prompt"; exit 1; }; echo
    read -r -s -p "confirm password: "       P2 || { log "FAIL: stdin closed"; exit 1; }; echo
    [ "$P1" = "$P2" ] && [ -n "$P1" ] && break
    log "passwords differ/empty — retry"
  done
  read -r -s -p "LiteLLM team API key for team '$TEAM' (alias $KEY_ALIAS): " TEAM_KEY; echo
  [ -n "$TEAM_KEY" ] || { log "FAIL: empty team key"; exit 1; }
  if [ "$SKIP_GRANT" = 0 ]; then
    read -r -s -p "LiteLLM master key (memory-only, never written): " MASTER_KEY; echo
  else MASTER_KEY=""; fi
  fi
  NAME=$(short_name "$HOST_NEW"); TEAM=$NAME; KEY_ALIAS=$(team_alias "$HOST_NEW")
  WEB_USER=${WEB_USER_PRESET:-${WEB_USER:-$(short_name "$HOST_NEW")}}
  log "inputs: host=$HOST_NEW name=$NAME ip=$LAN_IP team=$TEAM key_alias=$KEY_ALIAS web_user=$WEB_USER (secrets: held in memory only)"
}

stage_2() {
  log "== stage 2: identity =="
  if [ "$(hostname)" != "$HOST_NEW" ]; then srun hostnamectl set-hostname "$HOST_NEW"; fi
  if [ "$DRY" != 1 ]; then sudo cp -a /etc/hosts "$BACKUP_DIR/etc-hosts" 2>>"$LOGFILE"; fi
  hosts_render /etc/hosts "$LAN_IP" "$HOST_NEW" > /tmp/clone-prep-hosts.$$ \
    && srun cp /tmp/clone-prep-hosts.$$ /etc/hosts && rm -f /tmp/clone-prep-hosts.$$
  srun systemd-machine-id-setup
  [ -f /var/lib/dbus/machine-id ] && srun cp /etc/machine-id /var/lib/dbus/machine-id
  run rm -f "$HERMES/install_id"   # hermes regenerates on next start
  log "identity: hostname=$(hostname) machine-id=$(cat /etc/machine-id)"
}

stage_3() {
  log "== stage 3: ssh hygiene =="
  srun sh -c "rm -f /etc/ssh/ssh_host_*"
  srun ssh-keygen -A
  if [ -f "$HOME/.ssh/authorized_keys" ]; then
    run cp -a "$HOME/.ssh/authorized_keys" "$BACKUP_DIR/authorized_keys"
  fi
  printf '%s\n' "$DEPLOY_KEY_PUB" > /tmp/clone-prep-ak.$$
  run install -m 600 /tmp/clone-prep-ak.$$ "$HOME/.ssh/authorized_keys" && rm -f /tmp/clone-prep-ak.$$
  run sh -c ": > $HOME/.ssh/known_hosts"
  log "host keys: $(ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub 2>/dev/null)"
  log "authorized_keys: $(wc -l < "$HOME/.ssh/authorized_keys") key(s) (expect 1: nexa deploy)"
}

stage_4() {
  log "== stage 4: template-residue wipe =="
  run mkdir -p "$BACKUP_DIR" && run chmod 700 "$BACKUP_DIR"
  local items=(sessions state.db state.db-shm state.db-wal state.db.fts_rebuild.lock
    state.db.quarantine.lock state memories kanban kanban.db kanban.db-shm kanban.db-wal
    kanban.db.dispatch.lock kanban.db.init.lock projects.db cron gateway_state.json
    gateway-starts.log pairing pending_messages spawn-ledger.json spawn-ledger.json.lock
    channel_directory.json telemetry models_dev_cache.json models_dev_cache.etag
    provider_models_cache.json provider_models_cache.json.lock spawn-ledger.json)
  local it
  for it in "${items[@]}"; do
    [ -e "$HERMES/$it" ] || continue
    run mv "$HERMES/$it" "$BACKUP_DIR/"
  done
  # DB rows: truncate everything, keep the schemas (Jan 2026-11-11)
  local db
  for db in mnemo2db lcm2db; do
    if [ "$DRY" = 1 ]; then log "DRY: truncate all tables in lcm2_*/mnemo2_*/shared of $db"; continue; fi
    cat > /tmp/clone-prep-trunc.$$ <<'SQL'
DO $$
DECLARE r record;
BEGIN
  FOR r IN SELECT schemaname, tablename FROM pg_tables
           WHERE schemaname ~ '^(lcm2_|mnemo2_|shared)' LOOP
    -- CASCADE: mnemo2/lcm2 tables carry FK refs; bare TRUNCATE fails on the
    -- first FK-referenced table (e2e buddyos-jarvis 2026-11-12).
    EXECUTE format('TRUNCATE TABLE %I.%I RESTART IDENTITY CASCADE', r.schemaname, r.tablename);
  END LOOP;
END $$;
SQL
    "$PGBIN/psql" -h "$BASE/run" -p "$PORT" -U "$ROLE" -d "$db" \
      -v ON_ERROR_STOP=1 -f /tmp/clone-prep-trunc.$$ >>"$LOGFILE" 2>&1 \
      || gate db_wipe FAIL "truncate failed on $db"
    rm -f /tmp/clone-prep-trunc.$$
  done
  log "wipe done; backup: $BACKUP_DIR"
}

stage_5() {
  log "== stage 5: keys + webapp config =="
  if [ "$DRY" = 1 ]; then log "DRY: write team key to $HERMES/.env (key_env)"; else
    printf '%s' "$TEAM_KEY" | env_set_key "$HERMES/.env" HERMES_CUSTOM_AI_NEXASKILLS_CO_ZA_API_KEY
    log ".env key_env written (value never logged)"
  fi
  [ -f "$HERMES/auth.json" ] && [ "$DRY" != 1 ] && { py_json_reset_credpool "$HERMES/auth.json"; log "auth.json credential pool reset"; }
  run "$BIN" config set webapp.enabled true
  run "$BIN" config set webapp.host 0.0.0.0
  run "$BIN" config set dashboard.basic_auth.username "$WEB_USER"
  if [ "$DRY" = 1 ]; then
    log "DRY: set dashboard.basic_auth.password_hash (hash of entered password)"
  else
    local hash
    hash=$(printf '%s\n' "$P1" | hash_password) || { gate webapp FAIL "hash provider missing"; return; }
    printf '%s' "$hash" > /tmp/clone-prep-hash.$$
    run "$BIN" config set dashboard.basic_auth.password_hash "$(cat /tmp/clone-prep-hash.$$)"
    rm -f /tmp/clone-prep-hash.$$
    unset hash
  fi
  unset P1 P2
  run "$BIN" webapp --build-only
}

stage_6() {
  log "== stage 6: LiteLLM /kev grant (team $TEAM) =="
  if [ "$SKIP_GRANT" = 1 ]; then log "skipped (--skip-grant)"; return 0; fi
  local code; code=$(probe_kev)
  log "probe /kev/systemone -> $code"
  if [ "$code" = 200 ]; then log "already granted"; gate kev_grant PASS "probe 200"; return 0; fi
  if [ -z "$MASTER_KEY" ]; then gate kev_grant FAIL "403 and no master key"; return 0; fi
  # team_id by alias (never the master key in argv: -K - reads config on stdin)
  local tjson team_id meta code2
  if [ "$DRY" = 1 ]; then tjson="[]"; else
    tjson=$(printf 'header = "Authorization: Bearer %s"\nurl = "%s"\nsilent\n' \
              "$MASTER_KEY" "$LITELLM_URL/v2/team/list" | curl -K - 2>>"$LOGFILE")
  fi
  team_id=$(printf '%s' "$tjson" | TEAM="$TEAM" "$PY" -c '
import json,sys,os
d=json.load(sys.stdin)
teams=d if isinstance(d,list) else (d.get("teams") or [])
for t in teams:
    if t.get("team_alias")==os.environ["TEAM"]:
        print(t.get("team_id")); break' 2>>"$LOGFILE")
  [ "$DRY" = 1 ] && team_id=DRY
  [ -n "$team_id" ] || { gate kev_grant FAIL "team alias '$TEAM' not found on gateway"; return 0; }
  meta=$(printf '%s' "$tjson" | TEAM="$TEAM" "$PY" -c '
import json,sys,os
d=json.load(sys.stdin)
teams=d if isinstance(d,list) else (d.get("teams") or [])
for t in teams:
    if t.get("team_alias")==os.environ["TEAM"]:
        m=dict(t.get("metadata") or {})
        r=list(m.get("allowed_passthrough_routes") or [])
        if "/kev" not in r: r.append("/kev")
        m["allowed_passthrough_routes"]=r
        print(json.dumps({"team_id":t.get("team_id"),"metadata":m})); break' 2>>"$LOGFILE")
  [ "$DRY" = 1 ] && meta='{"team_id":"DRY","metadata":{"allowed_passthrough_routes":["/kev"]}}'
  code2=$(curl_stdin_header "$LITELLM_URL/team/update" "Authorization: Bearer $MASTER_KEY" \
           -X POST -H 'Content-Type: application/json' -d "$meta")
  code=$(probe_kev)
  log "after grant probe -> $code (team/update http $code2)"
  if [ "$code" = 200 ]; then gate kev_grant PASS "probe 200"; else gate kev_grant FAIL "probe $code"; fi
  unset MASTER_KEY tjson meta
}

stage_7() {
  log "== stage 7: solution self-heal (git pull IS the deploy) =="
  local d
  for d in "$HERMES"/checkout/*/; do
    [ -d "$d/.git" ] || continue
    run git -C "$d" pull --ff-only
    if [ -x "$d/scripts/rollout_update.sh" ]; then run bash "$d/scripts/rollout_update.sh"; fi
    if [ -x "$d/scripts/update_mnemo_host.sh" ]; then run bash "$d/scripts/update_mnemo_host.sh"; fi
    log "$(basename "$d"): $(git -C "$d" rev-parse --short HEAD)"
  done
}

stage_8() {
  log "== stage 8: gateway bounce =="
  local pid0 ts0 pid1 ts1
  pid0=$(systemctl --user show -p MainPID --value hermes-gateway)
  ts0=$(systemctl --user show -p ActiveEnterTimestamp --value hermes-gateway)
  run systemctl --user restart hermes-gateway
  local t0=$(date +%s) now
  while :; do
    sleep 3
    pid1=$(systemctl --user show -p MainPID --value hermes-gateway)
    ts1=$(systemctl --user show -p ActiveEnterTimestamp --value hermes-gateway)
    [ "$pid1" != "$pid0" ] && [ "$ts1" != "$ts0" ] && [ "$pid1" != 0 ] && break
    now=$(date +%s); [ $((now - t0)) -gt 90 ] && { gate boot FAIL "gateway did not recycle within 90s"; return 1; }
  done
  log "gateway recycled: pid $pid0 -> $pid1 (start $ts1)"
  local line
  line=$(grep -E "registered context engine" "$HERMES/logs/agent.log" | tail -1)
  case $line in *"engine: lcm2-pg"*) gate boot PASS "trio: $line";; *) gate boot FAIL "wrong engine: $line";; esac
  grep -qE "registered tool kev_decide" "$HERMES/logs/agent.log" || gate boot FAIL "kev_decide missing"
  grep -qE "memory-router hook" "$HERMES/logs/agent.log" || gate boot FAIL "memory-router hook missing"
}

stage_9() {
  log "== stage 9: gates =="
  if [ "$DRY" = 1 ]; then log "gates skipped in dry-run"; return 0; fi
  ss -ltn | grep -q "0.0.0.0:9120" && gate listen9120 PASS || gate listen9120 FAIL
  local c
  c=$(curl -s -o /dev/null -w '%{http_code}' "http://$LAN_IP:9120/")
  [ "$c" = 302 ] && gate webapp_lan PASS "http://$LAN_IP:9120/ -> 302" || gate webapp_lan FAIL "-> $c (expect 302; check ufw)"
  bash "$HERMES/checkout/lcm2-mnemo2-pg/scripts/bootstrap_verify.sh" default 2>>"$LOGFILE" \
    | tail -1 | grep -q "BOOTSTRAP_VERIFY OK" && gate bootstrap PASS || gate bootstrap FAIL
  bash "$HERMES/checkout/lcm2-mnemo2-pg/scripts/check_mnemo.sh" 2>&1 | tail -3 > /tmp/clone-prep-cm.$$
  tail -3 /tmp/clone-prep-cm.$$ | grep -qE "FAIL[= ]0" && gate check_mnemo PASS "$(tail -1 /tmp/clone-prep-cm.$$)" \
    || gate check_mnemo FAIL "$(tail -1 /tmp/clone-prep-cm.$$)"
  rm -f /tmp/clone-prep-cm.$$
  local c2; c2=$(probe_kev)
  [ "$c2" = 200 ] && gate kev_live PASS || gate kev_live FAIL "probe $c2"
  log "next (from buddyos-nexa): ssh mybuddy@$HOST_NEW (accept-new host key) + curl http://$LAN_IP:9120/ + repin known_hosts"
}

stage() { local n=$1; "stage_$n"; }

main() {
  mkdir -p "$LOGDIR" 2>/dev/null
  log "clone-prep start (dry-run=$DRY) host=$(hostname) user=$(whoami)"
  run mkdir -p "$BACKUP_DIR" && run chmod 700 "$BACKUP_DIR"
  local s
  for s in 0 1 2 3 4 5 6 7 8 9; do
    [ -n "$ONLY_STAGE" ] && [ "$s" != "$ONLY_STAGE" ] && continue
    stage "$s" || { log "stage $s failed — aborting"; break; }
  done
  log "gates: ${GATES[*]:-none}"
  [ "$FAIL_ANY" = 1 ] && { log "RESULT: FAIL"; exit 1; }
  log "RESULT: PASS"
}

# ---------- self-test (pure helpers only; touches nothing) ----------
self_test() {
  local fails=0
  ck() { if eval "$1"; then echo "  ok  - $2"; else echo "  FAIL- $2"; fails=$((fails+1)); fi; }
  ck "validate_hostname buddyos-newclone"            "accept buddyos-newclone"
  ck "! validate_hostname BUDDYOS-x"                   "reject uppercase"
  ck "! validate_hostname 'buddyos-'"                  "reject empty name"
  ck "! validate_hostname 'template'"                  "reject non-prefixed"
  ck "! validate_hostname 'buddyos-a..b'"              "reject dots"
  ck "[ \"\$(short_name buddyos-newclone)\" = newclone ]"  "short_name"
  ck "[ \"\$(team_alias buddyos-newclone)\" = newclone-hermes ]" "key alias <name>-hermes"
  ck "is_ipv4 192.168.1.99"                            "ipv4 ok"
  ck "! is_ipv4 300.1.1.1"                             "ipv4 reject"
  local t=$(mktemp)
  printf '127.0.0.1 localhost\n192.168.1.41 buddyos-template\n' > "$t"
  local out; out=$(hosts_render "$t" 192.168.1.99 buddyos-newclone)
  ck "! printf '%s' \"\$out\" | grep -q buddyos-template"  "hosts drops stale line"
  ck "printf '%s' \"\$out\" | grep -q 'buddyos-newclone'" "hosts adds new line"
  ck "printf '%s' \"\$out\" | grep -q '127.0.0.1 localhost'" "hosts keeps comment/loopback"
  printf 'OTHER=keep\nHERMES_CUSTOM_AI_NEXASKILLS_CO_ZA_API_KEY=old\n' > "$t"
  printf 'sk-newvalue\n' | env_set_key "$t" HERMES_CUSTOM_AI_NEXASKILLS_CO_ZA_API_KEY
  ck "grep -q 'sk-newvalue' $t"                        "env_set_key replaces"
  ck "grep -q 'OTHER=keep' $t"                        "env_set_key keeps others"
  ck "[ \"\$(stat -c %a $t)\" = 600 ]"                 "env_set_key chmod 600"
  rm -f "$t"
  local h; h=$(printf 'test-pw-123\n' | hash_password)
  ck "[[ \$h =~ ^scrypt\\\$16384\\\$8\\\$1\\\$[A-Za-z0-9+/=]+\\\$[A-Za-z0-9+/=]+\$ ]]" "hash shape (vendor scrypt format)"
  if [ "$fails" = 0 ]; then echo "SELFTEST OK"; else echo "SELFTEST FAIL ($fails)"; exit 1; fi
}

if [ "$SOURCE_ONLY" = 1 ]; then return 0 2>/dev/null || true; fi
if [ "$SELFTEST" = 1 ]; then mkdir -p "$LOGDIR" 2>/dev/null || true; self_test; exit 0; fi
main
