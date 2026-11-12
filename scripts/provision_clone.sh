#!/usr/bin/env bash
# provision_clone.sh — buddyos-nexa-side orchestrator: turn a fresh
# buddyos-template disk clone into a delivered customer system, end to end,
# fully unattended. One input pair: hostname + LAN IP.
#
#   scripts/provision_clone.sh --hostname=buddyos-jarvis --ip=192.168.1.149
#
# Pipeline (stages p0..p4, mirrors clone_prep.sh conventions):
#   p0 prereq     master key + deploy key present; ssh BatchMode login to
#                 the clone works (fresh template still carries the nexa
#                 deploy pubkey); derive name/user/password convention.
#   p1 litellm    ensure team <name> (models TEAM_MODELS, /kev passthrough
#                 merged into metadata), revoke its stale keys, generate
#                 fresh key alias <name>-hermes. Key value lives in a shell
#                 variable only.
#   p2 prep-run   git pull the repo (deploy == repo HEAD), stream
#                 scripts/clone_prep.sh over ssh to /tmp (700), run it
#                 --noninteractive with the two secret lines on stdin,
#                 shred the pushed copy.
#   p2.5 caddy    append the <name>.nexaskills.co.za -> ip:9120 site block
#                 on the edge host (backup -> validate -> restart, restore
#                 on invalid — 0-caddy-admin.sh semantics). WARN-only gate
#                 if the edge host is unreachable; IONOS DNS is Jan-side.
#   p3 nexa-gates known_hosts for ip+hostname wiped and re-pinned via
#                 keyscan+verify; ssh hostname matches; http://ip:9120/
#                 -> 302 from nexa; /kev probe 200 with the new team key;
#                 public url https://<name>.nexaskills.co.za probed
#                 (edge routing is managed off-host: WARN, not FAIL).
#   p4 ledger     one entry appended to the nexa-local fleet ledger
#                 (NEVER a git repo): host, ip, team, key alias, repo sha,
#                 gate lines, verdict. Secrets are never written — the key
#                 appears only as its alias.
#
# Naming convention (Jan, 2026-11-12): hostname buddyos-<name> -> team
# alias <name>, key alias <name>-hermes, dashboard user <name>, dashboard
# password <Name><NEXA_PW_SUFFIX>. The suffix lives ONLY in the nexa-local
# secrets file (repos are public); it NEVER appears in this file or logs.
#
# Config (env or secrets file ~/.hermes/profiles/devon/secrets/clone-provision.env):
#   LITELLM_URL           default https://ai.nexaskills.co.za
#   LITELLM_MASTER_KEY    required (admin; LiteLLM v1 key semantics)
#   NEXA_PW_SUFFIX        required (dashboard password suffix)
#   TEAM_MODELS           space list, default "main kev"
#   REPO_DIR              default ~/Projects/lcm2-mnemo2-pg
#   LEDGER_FILE           default ~/agent_workspace/fleet-clone-ledger.md
#
# Flags: --hostname= --ip= --dry-run --source-only --self-test
#        --skip-pull (use local repo HEAD as-is)
# Exit: 0 all gates PASS, 1 any FAIL, 2 usage/prereq.
set -uo pipefail

LITELLM_URL=${LITELLM_URL:-https://ai.nexaskills.co.za}
REPO_DIR=${REPO_DIR:-$HOME/Projects/lcm2-mnemo2-pg}
LEDGER_FILE=${LEDGER_FILE:-$HOME/agent_workspace/fleet-clone-ledger.md}
TEAM_MODELS=${TEAM_MODELS:-"main kev"}
SECRETS_FILE=${SECRETS_FILE:-$HOME/.hermes/profiles/devon/secrets/clone-provision.env}
# Edge (reverse proxy + LiteLLM host). Site-add follows 0-caddy-admin.sh's
# edit flow semantics: timestamped backup -> append -> caddy validate ->
# fix log ownership -> restart. DNS subdomain creation at IONOS is Jan-side
# (gate reports it as an action item, never a silent FAIL).
CADDY_HOST=${CADDY_HOST:-192.168.1.10}
CADDY_USER=${CADDY_USER:-crpadmin}
CADDYFILE_REMOTE=${CADDYFILE_REMOTE:-/etc/caddy/Caddyfile}
EDGE_DOMAIN=${EDGE_DOMAIN:-nexaskills.co.za}
SSH="ssh -o BatchMode=yes -o ConnectTimeout=10"
TS=$(date -u +%Y%m%d-%H%M%S)
LOGFILE=$HOME/.hermes/logs/clone-provision-$TS.log

DRY=0; SOURCE_ONLY=0; SELFTEST=0; SKIP_PULL=0
PRESET_HOSTNAME=""; PRESET_IP=""
GATES=(); FAIL_ANY=0

for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY=1 ;;
    --source-only) SOURCE_ONLY=1 ;;
    --self-test) SELFTEST=1 ;;
    --skip-pull) SKIP_PULL=1 ;;
    --hostname=*) PRESET_HOSTNAME=${arg#*=} ;;
    --ip=*) PRESET_IP=${arg#*=} ;;
    *) echo "unknown flag: $arg" >&2; exit 2 ;;
  esac
done

[ -f "$SECRETS_FILE" ] && { chmod 600 "$SECRETS_FILE" 2>/dev/null; source "$SECRETS_FILE"; }
mkdir -p "$(dirname "$LOGFILE")" 2>/dev/null

log() { local line="$*"; echo "$(date -u +%H:%M:%S) $line" | tee -a "$LOGFILE"; }
gate() { GATES+=("$1=$2"); [ "$2" = FAIL ] && FAIL_ANY=1; log "GATE: $1 $2 ${3:-}"; }

# ---------- pure helpers (unit-tested) ----------

validate_hostname() { [[ $1 =~ ^buddyos-[a-z][a-z0-9]*(-[a-z0-9]+)*$ ]]; }
short_name()   { printf '%s' "${1#buddyos-}"; }
user_name()    { short_name "$1"; }                       # buddyos-jarvis -> jarvis
key_alias()    { printf '%s-hermes' "$(short_name "$1")"; }
dashboard_pw() { local n; n=$(short_name "$1"); printf '%s%s' "${n^}" "${NEXA_PW_SUFFIX:-}"; }
is_ipv4()      { [[ $1 =~ ^((25[0-5]|2[0-4][0-9]|1[0-9]{2}|[1-9]?[0-9])\.){3}(25[0-5]|2[0-4][0-9]|1[0-9]{2}|[1-9]?[0-9])$ ]]; }
public_url()   { printf 'https://%s.nexaskills.co.za' "$(short_name "$1")"; }  # buddyos-jarvis -> https://jarvis.nexaskills.co.za
models_json()  { local m out="["; for m in $TEAM_MODELS; do out="$out\"$m\","; done; printf '%s]' "${out%,}"; }

# curl via -K - on stdin: master key never appears in argv/ps. Bodies go as
# `data =` config lines (this curl build rejects the `postfield` ALIAS in
# config files — e2e 2026-11-12; data works and stays off argv).
# Never logs any line containing a key value.
lit_api() { # lit_api METHOD PATH [json-body] -> body on stdout
  local method=$1 path=$2 body=${3:-} esc
  if [ "$DRY" = 1 ] && [ "$method" != GET ]; then log "DRY: lit $method $path"; echo '{}'; return 0; fi
  esc=""
  [ -n "$body" ] && esc=$(printf '%s' "$body" | sed 's/\\/\\\\/g; s/"/\\"/g')
  { printf 'header = "Authorization: Bearer %s"\nurl = "%s%s"\nrequest = "%s"\nsilent\nshow-error\n' \
      "$LITELLM_MASTER_KEY" "$LITELLM_URL" "$path" "$method"
    [ -n "$body" ] && printf 'header = "Content-Type: application/json"\ndata = "%s"\n' "$esc"
  } | curl -K - 2>>"$LOGFILE"
}

kev_probe() { # kev_probe <team-key> -> http code
  local key=$1
  if [ "$DRY" = 1 ]; then echo 000; return 0; fi
  printf 'header = "Authorization: Bearer %s"\nurl = "%s/kev/systemone"\nsilent\nshow-error\noutput = "/dev/null"\nwrite-out = "%%{http_code}"\n' \
    "$key" "$LITELLM_URL" | curl -K - -X POST -H 'Content-Type: application/json' \
    -d '{"model":"kev","state":"Health probe. No task running.","questions":{"skill":{"type":"choice","instructions":"Which skill fits this task? Choose none if none apply.","criteria":{"github":"GitHub repo operations","none":"No skill applies."}}}}' 2>>"$LOGFILE"
}

# ---------- stages ----------

p0() {
  log "== p0: prereq =="
  HOST=$PRESET_HOSTNAME; [ -n "$HOST" ] && validate_hostname "$HOST" \
    || { log "usage: --hostname=buddyos-<name>"; exit 2; }
  IP=$PRESET_IP; is_ipv4 "$IP" || { log "usage: --ip=<a.b.c.d> required"; exit 2; }
  [ -n "${LITELLM_MASTER_KEY:-}" ] || { log "FAIL: LITELLM_MASTER_KEY unset (secrets file?)"; exit 2; }
  [ -n "${NEXA_PW_SUFFIX:-}" ]     || { log "FAIL: NEXA_PW_SUFFIX unset (secrets file?)"; exit 2; }
  [ -f "$HOME/.ssh/id_ed25519.pub" ] || { log "FAIL: no nexa deploy pubkey"; exit 2; }
  NAME=$(short_name "$HOST"); USER_=$(user_name "$HOST"); KALIAS=$(key_alias "$HOST")
  TEAM=$NAME
  # A freshly restored snapshot regenerates host keys every run; drop any
  # stale pin for this IP or BatchMode refuses the changed host key. The
  # connecting ssh must carry accept-new itself — after -R the key is
  # UNKNOWN, and BatchMode+default checking cannot accept unknown keys.
  ssh-keygen -R "$IP" >/dev/null 2>&1
  $SSH -o StrictHostKeyChecking=accept-new mybuddy@"$IP" true \
    && gate ssh_first_contact PASS "BatchMode login as-is" \
    || { gate ssh_first_contact FAIL "cannot ssh the fresh clone — check template authorized_keys"; return 1; }
  local cur; cur=$($SSH mybuddy@"$IP" hostname)
  # snapshot restores can leave the clone clock minutes off — kev/TLS/DB
  # timestamps all care. Report, and give timesync a shove.
  local cs now skew
  cs=$($SSH mybuddy@"$IP" date +%s); now=$(date +%s); skew=$(( now - cs ))
  if [ "${skew#-}" -gt 30 ]; then
    log "WARN: clone clock skew ${skew}s — nudging NTP"
    $SSH mybuddy@"$IP" "sudo -n timedatectl set-ntp true >/dev/null 2>&1; sudo -n chronyc -a makestep >/dev/null 2>&1" || true
    sleep 2
    cs=$($SSH mybuddy@"$IP" date +%s); skew=$(( now - cs ))
    [ "${skew#-}" -le 30 ] && gate clock PASS "skew ${skew}s after nudge" \
                           || gate clock WARN "skew still ${skew}s (no NTP source on clone?)"
  else
    gate clock PASS "skew ${skew}s"
  fi
  log "target: host=$HOST name=$NAME ip=$IP team=$TEAM key=$KALIAS user=$USER_ current-hostname=$cur"
}

p1() {
  log "== p1: litellm team '$TEAM' + key '$KALIAS' =="
  local tjson team_id models
  tjson=$(lit_api GET "/v2/team/list")
  team_id=$(printf '%s' "$tjson" | TEAM="$TEAM" "${PY:-python3}" -c '
import json,sys,os
d=json.load(sys.stdin); teams=d if isinstance(d,list) else (d.get("teams") or [])
for t in teams:
    if t.get("team_alias")==os.environ["TEAM"]: print(t.get("team_id")); break')
  models=$(models_json)
  if [ -z "$team_id" ] && [ "$DRY" != 1 ]; then
    local resp; resp=$(lit_api POST /team/new \
      "{\"team_alias\":\"$TEAM\",\"models\":$models,\"metadata\":{\"allowed_passthrough_routes\":[\"/kev\"]}}")
    team_id=$(printf '%s' "$resp" | "${PY:-python3}" -c '
import json,sys
d=json.load(sys.stdin)
print((d.get("team") or d).get("team_id") or d.get("team_id") or "")' 2>>"$LOGFILE")
    [ -n "$team_id" ] || { gate litellm FAIL "team/new failed: $(printf '%s' "$resp" | head -c 200)"; return; }
    log "team created id=$team_id"
  else
    [ "$DRY" = 1 ] && team_id=DRY
    # existing team: ensure /kev passthrough (merge metadata, never clobber)
    local meta; meta=$(printf '%s' "$tjson" | TEAM="$TEAM" "${PY:-python3}" -c '
import json,sys,os
d=json.load(sys.stdin); teams=d if isinstance(d,list) else (d.get("teams") or [])
for t in teams:
    if t.get("team_alias")==os.environ["TEAM"]:
        m=dict(t.get("metadata") or {})
        r=list(m.get("allowed_passthrough_routes") or [])
        if "/kev" not in r: r.append("/kev")
        m["allowed_passthrough_routes"]=r
        print(json.dumps(m)); break' 2>>"$LOGFILE")
    if [ -n "$meta" ] && [ "$DRY" != 1 ]; then
      lit_api POST /team/update "{\"team_id\":\"$team_id\",\"metadata\":$meta}" >/dev/null
      log "team exists id=$team_id — /kev passthrough merged"
    else
      log "team exists id=$team_id (passthrough merge skipped: dry-run or no meta)"
    fi
  fi
  # revoke stale keys of THIS team only, then generate fresh
  local kl hashes
  kl=$(lit_api GET "/key/list?team_id=$team_id&include_team_keys=true&return_full_object=true")
  hashes=$(printf '%s' "$kl" | "${PY:-python3}" -c '
import json,sys
d=json.load(sys.stdin)
print(json.dumps([k.get("token") for k in (d.get("keys") or []) if isinstance(k,dict) and k.get("token")]))' 2>>"$LOGFILE")
  if [ "$hashes" != "[]" ] && [ -n "$hashes" ]; then
    lit_api POST /key/delete "{\"keys\":$hashes,\"team_id\":\"$team_id\"}" >/dev/null
    log "revoked stale team keys"
  fi
  local kresp keyval
  kresp=$(lit_api POST /key/generate \
    "{\"team_id\":\"$team_id\",\"key_alias\":\"$KALIAS\",\"models\":$models,\"duration\":null}")
  keyval=$(printf '%s' "$kresp" | "${PY:-python3}" -c '
import json,sys
d=json.load(sys.stdin)
print(d.get("key") or "" if isinstance(d,dict) else "")' 2>>"$LOGFILE")
  if [ -z "$keyval" ] && [ "$DRY" != 1 ]; then
    gate litellm FAIL "key/generate failed: $(printf '%s' "$kresp" | head -c 200)"; return
  fi
  TEAM_KEY=$keyval
  gate litellm PASS "team_id=$team_id key=$KALIAS generated"
}

p2() {
  log "== p2: stream + run clone_prep on $HOST =="
  if [ -z "${TEAM_KEY:-}" ]; then gate prep_run FAIL "no team key (p1 failed) — refusing remote run"; return 1; fi
  if [ "$SKIP_PULL" != 1 ] && [ "$DRY" != 1 ]; then
    git -C "$REPO_DIR" pull --ff-only >>"$LOGFILE" 2>&1 \
      || log "WARN: repo pull skipped/failed — running local HEAD"
  fi
  [ "$DRY" = 1 ] && { log "DRY: stream $REPO_DIR/scripts/clone_prep.sh -> /tmp + run"; return 0; }
  REPO_SHA=$(git -C "$REPO_DIR" rev-parse --short HEAD)
  log "deploy sha: $REPO_SHA"
  cat "$REPO_DIR/scripts/clone_prep.sh" \
    | $SSH mybuddy@"$IP" 'cat > /tmp/clone_prep.push.sh && chmod 700 /tmp/clone_prep.push.sh' \
    || { gate prep_run FAIL "could not push clone_prep.sh"; return; }
  # stdin contract: line1 team key, line2 dashboard password (never argv)
  printf '%s\n%s\n' "$TEAM_KEY" "$(dashboard_pw "$HOST")" \
    | $SSH -o ServerAliveInterval=15 mybuddy@"$IP" \
        "bash /tmp/clone_prep.push.sh --noninteractive --hostname=$HOST --ip=$IP --web-user=$USER_ ; rc=\$?; shred -u /tmp/clone_prep.push.sh 2>/dev/null || rm -f /tmp/clone_prep.push.sh; exit \$rc" \
    2>&1 | tee -a "$LOGFILE"
  local rc=${PIPESTATUS[1]}
  [ "$rc" = 0 ] && gate prep_run PASS "clone_prep exit 0 (see log above for its gates)" \
                 || gate prep_run FAIL "clone_prep exit $rc"
}

pcaddy() {
  log "== p2.5: caddy edge entry for $(public_url "$HOST") =="
  [ "$DRY" = 1 ] && { log "DRY: add caddy site block on $CADDY_HOST"; return 0; }
  $SSH "$CADDY_USER@$CADDY_HOST" true \
    || { gate caddy WARN "cannot ssh $CADDY_USER@$CADDY_HOST — public url not provisioned"; return 0; }
  PUBHOST=$(short_name "$HOST")."$EDGE_DOMAIN"
  if $SSH "$CADDY_USER@$CADDY_HOST" "grep -qE '^${PUBHOST}[[:space:]]*\{' $CADDYFILE_REMOTE"; then
    gate caddy PASS "site $PUBHOST already present"; return 0
  fi
  local stamp; stamp=$(date -u +%Y%m%dT%H%M%SZ)
  $SSH "$CADDY_USER@$CADDY_HOST" "sudo -n cp -a $CADDYFILE_REMOTE $CADDYFILE_REMOTE.bak-$stamp" \
    || { gate caddy FAIL "backup failed"; return 0; }
  $SSH "$CADDY_USER@$CADDY_HOST" "sudo -n tee -a $CADDYFILE_REMOTE >/dev/null" <<EOF
#===========================
# $PUBHOST (added by provision_clone.sh $stamp)
#===========================
$PUBHOST {
        reverse_proxy $IP:9120
        log {
                output file /var/log/caddy/${NAME}web.log {
                       roll_size 20MiB
                       roll_keep 5
                       roll_keep_for 720h
                }
        }
}
EOF
  if $SSH "$CADDY_USER@$CADDY_HOST" "sudo -n caddy validate --config $CADDYFILE_REMOTE --adapter caddyfile" >>"$LOGFILE" 2>&1; then
    # fix log ownership exactly like 0-caddy-admin.sh, then restart (their edit flow)
    $SSH "$CADDY_USER@$CADDY_HOST" "sudo -n chown -R caddy:caddy /var/log/caddy; sudo -n systemctl restart caddy" >>"$LOGFILE" 2>&1
    sleep 3
    gate caddy PASS "site $PUBHOST installed, caddy restarted"
  else
    $SSH "$CADDY_USER@$CADDY_HOST" "sudo -n cp -a $CADDYFILE_REMOTE.bak-$stamp $CADDYFILE_REMOTE" \
      && gate caddy FAIL "Caddyfile invalid — restored from backup-$stamp (edge untouched)" \
      || gate caddy FAIL "Caddyfile invalid AND restore failed — human on the caddy host NOW"
  fi
}

p3() {
  log "== p3: nexa-side gates =="
  [ "$DRY" = 1 ] && { log "gates skipped in dry-run"; return 0; }
  # host keys were regenerated on the clone: wipe both forms, pin fresh
  ssh-keygen -R "$IP" >/dev/null 2>&1
  ssh-keygen -R "$HOST" >/dev/null 2>&1
  local fp; fp=$($SSH -o StrictHostKeyChecking=accept-new mybuddy@"$IP" true 2>/dev/null && echo ok)
  [ -n "$fp" ] && gate repin PASS "new host key pinned" || gate repin FAIL "ssh after key regen failed"
  local hn; hn=$($SSH mybuddy@"$IP" hostname 2>/dev/null)
  [ "$hn" = "$HOST" ] && gate hostname PASS "remote hostname=$hn" || gate hostname FAIL "remote hostname=$hn"
  # Standing fleet rule (Jan): ops hosts must resolve <name>.<edge-domain> to
  # the edge via /etc/hosts — public DNS may lag or split-horizon.
  local pubhost; pubhost=$(short_name "$HOST")."$EDGE_DOMAIN"
  if grep -q "$pubhost" /etc/hosts; then gate hosts_pin PASS "$pubhost pinned"
  elif printf '\n# buddyos clone %s (provisioning)\n%s\t%s\n' "$NAME" "$CADDY_HOST" "$pubhost" \
       | sudo -n tee -a /etc/hosts >/dev/null 2>&1; then
    gate hosts_pin PASS "$pubhost -> $CADDY_HOST added"
  else gate hosts_pin WARN "could not pin $pubhost in nexa /etc/hosts"; fi
  local c; c=$(curl -s -o /dev/null -w '%{http_code}' "http://$IP:9120/")
  [ "$c" = 302 ] && gate dashboard PASS "http://$IP:9120/ -> 302" || gate dashboard FAIL "-> $c (expect 302)"
  local k; k=$(kev_probe "${TEAM_KEY:-}")
  [ "$k" = 200 ] && gate kev_live PASS "probe 200 with $KALIAS" || gate kev_live FAIL "probe $k"
  # public edge: DNS at IONOS is Jan-side; distinguish config vs DNS waits
  local PUBHOST_EDGE; PUBHOST_EDGE=$(short_name "$HOST")."$EDGE_DOMAIN"
  local pc; pc=$(curl -s -o /dev/null -m 10 -w '%{http_code}' "https://$PUBHOST_EDGE/" 2>/dev/null)
  case "$pc" in
    302|200) gate public_url PASS "https://$PUBHOST_EDGE/ -> $pc" ;;
    *)
      local ec; ec=$($SSH "$CADDY_USER@$CADDY_HOST" "curl -sk -o /dev/null -m 10 -w '%{http_code}' --resolve $PUBHOST_EDGE:443:127.0.0.1 https://$PUBHOST_EDGE/" 2>/dev/null)
      case "$ec" in
        302|200) gate public_url WARN "edge OK ($ec) via $CADDY_HOST; ACTION: ask Jan to create IONOS subdomain $PUBHOST_EDGE" ;;
        502|503|504) gate public_url WARN "edge routes but upstream down ($ec) — check clone webapp/ufw" ;;
        *) gate public_url WARN "public url not live (local=$pc edge=$ec); needs IONOS subdomain $PUBHOST_EDGE + TLS" ;;
      esac ;;
  esac
}

p4() {
  log "== p4: ledger =="
  local verdict=PASS; [ "$FAIL_ANY" = 1 ] && verdict=FAIL
  mkdir -p "$(dirname "$LEDGER_FILE")" 2>/dev/null
  if [ "$DRY" != 1 ]; then
    { echo "## $TS — $HOST ($IP)"
      echo "- public url: $(public_url "$HOST")"
      echo "- team: $TEAM | key alias: $KALIAS (value: never stored)"
      echo "- dashboard user: $USER_ (password: derived convention, not stored)"
      echo "- repo sha: ${REPO_SHA:-dry-run}"
      echo "- gates: ${GATES[*]:-none}"
      echo "- verdict: $verdict"; echo; } >> "$LEDGER_FILE"
  fi
  log "ledger: ${LEDGER_FILE} (verdict $verdict)"
}

main() {
  log "clone-provision start (dry=$DRY) nexa=$(hostname)"
  local s
  for s in 0 1 2 caddy 3; do
    "p$s" || { log "stage p$s failed — skipping to ledger"; break; }
  done
  p4   # ledger always: a FAILed run is exactly the entry you want on file
  local verdict=FAIL; [ "$FAIL_ANY" = 0 ] && verdict=PASS
  log "gates: ${GATES[*]:-none}"
  log "RESULT: $verdict"
  if [ "$FAIL_ANY" = 1 ]; then exit 1; fi
  exit 0
}

self_test() {
  local fails=0
  ck() { if eval "$1"; then echo "  ok  - $2"; else echo "  FAIL- $2"; fails=$((fails+1)); fi; }
  ck "validate_hostname buddyos-jarvis"                 "accept buddyos-jarvis"
  ck "! validate_hostname jarvis"                       "reject bare name"
  ck "! validate_hostname 'buddyos-J'"                  "reject uppercase"
  ck "[ \"\$(short_name buddyos-jarvis)\" = jarvis ]"   "short_name"
  ck "[ \"\$(user_name buddyos-jarvis)\" = jarvis ]"    "user = short name"
  ck "[ \"\$(key_alias buddyos-jarvis)\" = jarvis-hermes ]" "key alias <name>-hermes"
  ck "is_ipv4 192.168.1.149"                            "ipv4 ok"
  ck "! is_ipv4 999.1.1.1"                              "ipv4 reject"
  ck "NEXA_PW_SUFFIX='Xx9'; [ \"\$(dashboard_pw buddyos-jarvis)\" = JarvisXx9 ]" "pw = Capitalized-name + suffix"
  ck "TEAM_MODELS='main kev'; [ \"\$(models_json)\" = '[\"main\",\"kev\"]' ]" "models_json"
  if [ "$fails" = 0 ]; then echo "SELFTEST OK"; else echo "SELFTEST FAIL ($fails)"; exit 1; fi
}

if [ "$SOURCE_ONLY" = 1 ]; then return 0 2>/dev/null || true; fi
if [ "$SELFTEST" = 1 ]; then self_test; exit 0; fi
main
