"""A/B/C matrix on a fresh checkout of the checkpoint commit."""
import os, subprocess, uuid, shutil, tempfile

REPO = os.path.expanduser("~/lcm2-mnemo2-pg")
WT = tempfile.mkdtemp(prefix="r8wt-", dir=os.path.expanduser("~/.hermes/cache/scratch"))
subprocess.run(["git", "worktree", "add", "-f", "-d", WT, "HEAD"], cwd=REPO, check=True)

def run(files, env_extra, label):
    env = dict(os.environ)
    env.pop("LCM2_R8_RUN", None)
    env.update(env_extra)
    r = subprocess.run(["/home/mybuddy/lcm-pg/venv/bin/pytest", *files, "-q",
                        "--no-header", "-p", "no:cacheprovider"],
                       env=env, capture_output=True, text=True, cwd=WT)
    tail = r.stdout.strip().splitlines()[-1] if r.stdout.strip() else "?"
    print(f"{label:55s} exit={r.returncode} | {tail}")

try:
    # A: R8 alone, NO gate env (uuid RUN) — reproduces the 4x red?
    run(["tests/lcm2/test_r8_concurrency.py"], {}, "A: R8 alone, no LCM2_R8_RUN")
    # B: R8 alone with gate env
    run(["tests/lcm2/test_r8_concurrency.py"],
        {"LCM2_R8_RUN": "matb-" + uuid.uuid4().hex[:8]}, "B: R8 alone + gate env")
    # C: zz alone (no env)
    run(["tests/lcm2/test_zz_trace_r8.py"], {}, "C: zz alone, no env")
    # D: full suite no env
    run(["tests/lcm2"], {}, "D: full suite, no LCM2_R8_RUN")
    # E: R8 alone x3 sequential, no env (state buildup?)
    for i in range(3):
        run(["tests/lcm2/test_r8_concurrency.py"], {}, f"E{i}: R8 alone, no env")
finally:
    subprocess.run(["git", "worktree", "remove", "--force", WT], cwd=REPO, check=True)
