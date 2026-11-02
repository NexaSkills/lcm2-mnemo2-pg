#!/usr/bin/env bash
# fix7c: source this + export the vars to bind a run to the SPLIT instance
# (fresh PGDATA ~/lcm2-mnemo2-pg-split, port 54332) so an operator's lane is
# isolated from other live sessions on the primary :54331 instance.
# Created by scripts/setup_instance.sh with BASE=~/lcm2-mnemo2-pg-split
# PORT=54332 SERVICE=lcm2-mnemo2-split.service (unit exists; watchdog timer
# NOT installed — deliberately, to avoid cross-binding).
export LCM2_PG_HOST="$HOME/lcm2-mnemo2-pg-split/run"
export MNEMO2_PG_HOST="$HOME/lcm2-mnemo2-pg-split/run"
export LCM2_PG_PORT=54332
export MNEMO2_PG_PORT=54332
export LCM2_PG_DSN="dbname=lcm2db host=$HOME/lcm2-mnemo2-pg-split/run port=54332 user=mnemo201"
export MNEMO2_PG_DSN="dbname=mnemo2db host=$HOME/lcm2-mnemo2-pg-split/run port=54332 user=mnemo201"
