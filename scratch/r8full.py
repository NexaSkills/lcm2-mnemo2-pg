"""Reproduce the FULL-suite R8 failure deterministically if possible.

Runs every prior test module's collection/ordering effect, then R8 with a
unique RUN, mirroring the full-suite env (LCM2_DISABLE_MODEL, conftest
fixtures). Prints audit so we can see where the writes went.
"""
import subprocess, sys, os
cmd = ["/home/mybuddy/lcm-pg/venv/bin/pytest",
       "tests/lcm2/test_r3_no_sqlite.py", "tests/lcm2/test_r3_runtime_fallback.py",
       "tests/lcm2/test_r4_full_surface.py", "tests/lcm2/test_r4_table_parity.py",
       "tests/lcm2/test_r4_tools_surface.py", "tests/lcm2/test_r7_compression.py",
       "tests/lcm2/test_r8_concurrency.py", "tests/lcm2/test_zz_trace_r8.py",
       "-q", "-s", "--tb=line"]
env = dict(os.environ, LCM2_R8_RUN="fullrepro-" + str(os.getpid()))
r = subprocess.run(cmd, env=env, capture_output=True, text=True, cwd=os.path.expanduser("~/lcm2-mnemo2-pg"))
print(r.stdout[-3000:])
print("EXIT", r.returncode)
