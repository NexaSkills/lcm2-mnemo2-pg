#!/usr/bin/env bash
# Production full-suite run: default env + required gate envs (LCM2_R8_RUN).
set -u
cd "$HOME/lcm2-mnemo2-pg"
export LCM2_R8_RUN="gate$(date +%s)-$$"
echo "== env: LCM2_R8_RUN=$LCM2_R8_RUN =="
/home/mybuddy/lcm-pg/venv/bin/pytest tests/lcm2 -q 2>&1
echo "PYTEST_EXIT=$?"
