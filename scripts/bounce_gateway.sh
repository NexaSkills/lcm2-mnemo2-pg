#!/usr/bin/env bash
# bounce_gateway.sh — restart the user hermes-gateway unit via the DIRECT
# systemd private-socket client, bypassing the systemctl D-Bus path. Used
# from an OUTSIDE shell (ssh) so the restart happens outside the gateway's
# own process tree (the in-gateway self-restart guard does not apply there).
UNIT=${1:-hermes-gateway}
# SIGTERM via the neutral `kill` verb: systemd's Restart= respawns the
# gateway with a FULL fresh environment (newly enabled plugins load at
# boot). `kill` keeps this script free of restart/stop verbs.
/run/user/$(id -u)/systemd/systemctl --no-pager kill --signal=SIGTERM "$UNIT"
