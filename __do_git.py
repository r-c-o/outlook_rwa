import subprocess, sys

GIT = r"C:\Program Files\Git\cmd\git.exe"
REPO = r"C:\Users\ryanc\projects\outlook_rwa"

def run(args, **kw):
    r = subprocess.run([GIT, "-C", REPO] + args, capture_output=True, text=True, **kw)
    out = r.stdout + r.stderr
    with open(r"C:\Users\ryanc\projects\outlook_rwa\__git_log.txt", "a") as f:
        f.write(f"CMD: git {' '.join(args)}\nRC: {r.returncode}\n{out}\n---\n")
    return r.returncode, out

_, o = run(["status", "--short"])
_, o = run(["log", "--oneline", "-5"])
_, o = run(["add",
            "src/main/tools/step1_model_convergence.py",
            "config.toml"])
_, o = run(["commit", "-m",
            "Fix Int64 dtype compat in astype; add backup paths for data_dir and schema_registry_csv"])
_, o = run(["push", "origin", "master"])
